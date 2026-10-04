"""Local Ollama LLM access.

Nodes call ``llm.structured(...)`` and ``llm.generate(...)`` through the module
(``from content_agents import llm``) so tests can monkeypatch them.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Literal, Sequence, Type, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from .config import get_settings

log = logging.getLogger(__name__)

Role = Literal["writer", "fast", "script"]
T = TypeVar("T", bound=BaseModel)

# Model families that "think" by default; thinking is turned off for speed on CPU.
_THINKING_PREFIXES = ("qwen3", "deepseek-r1", "qwq", "magistral", "gpt-oss")
STRUCTURED_MAX_TOKENS = 3000  # the largest schema (a 9-section Plan) fits comfortably
_THINK_TAG = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(RuntimeError):
    pass


def model_for(role: Role) -> str:
    s = get_settings()
    return {"writer": s.writer_model, "fast": s.fast_model, "script": s.script_model}[role]


@lru_cache
def get_llm(role: Role = "writer", temperature: float | None = None, num_predict: int | None = None,
            repeat_penalty: float | None = None) -> ChatOllama:
    s = get_settings()
    model = model_for(role)
    if temperature is None:
        temperature = 0.2 if role == "fast" else 0.7
    kwargs = {}
    if model.lower().startswith(_THINKING_PREFIXES):
        kwargs["reasoning"] = False
    if num_predict:
        kwargs["num_predict"] = num_predict
    if repeat_penalty:
        kwargs["repeat_penalty"] = repeat_penalty
    return ChatOllama(
        model=model,
        base_url=s.ollama_base_url,
        num_ctx=s.fast_ctx if role == "fast" else s.writer_ctx,
        temperature=temperature,
        keep_alive="30m",
        client_kwargs={"timeout": s.llm_timeout},
        **kwargs,
    )


def clean_text(text: str) -> str:
    return _THINK_TAG.sub("", text or "").strip()


def generate(messages: Sequence[BaseMessage], role: Role = "writer", temperature: float | None = None) -> str:
    """Free-form text generation."""
    resp = get_llm(role, temperature).invoke(list(messages))
    return clean_text(resp.content if isinstance(resp.content, str) else str(resp.content))


def structured(
    schema: Type[T],
    messages: Sequence[BaseMessage],
    role: Role = "fast",
    retries: int = 2,
    temperature: float | None = None,
) -> T:
    """Structured output via Ollama JSON-schema mode, retrying with the validation error.

    Small models sometimes loop inside constrained JSON ("token repeat limit reached"), so output is capped
    and each retry runs warmer with a mild repeat penalty to break the loop.
    """
    base = temperature if temperature is not None else 0.2
    msgs = list(messages)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        runner = get_llm(
            role,
            round(min(base + 0.3 * attempt, 1.0), 2),
            num_predict=STRUCTURED_MAX_TOKENS,
            repeat_penalty=1.1 if attempt else None,
        ).with_structured_output(schema, method="json_schema")
        try:
            out = runner.invoke(msgs)
            if out is None:
                raise ValueError("model returned no parseable JSON")
            return out
        except Exception as e:  # validation or transport error
            last_err = e
            log.warning("structured(%s) attempt %d failed: %s", schema.__name__, attempt + 1, e)
            msgs = list(messages) + [
                HumanMessage(
                    content=(
                        f"Your previous answer was invalid: {str(e)[:500]}\n"
                        "Return ONLY valid JSON that matches the schema exactly."
                    )
                )
            ]
    raise StructuredOutputError(f"{schema.__name__}: {last_err}")
