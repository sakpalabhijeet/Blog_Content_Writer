"""Shared human-in-the-loop pieces for both agents.

Pattern per review step:  send_<x> node (emails, bumps review_seq)  ->  await_<x> node (interrupt only).
A node that calls interrupt() re-runs from the top on resume, so it must never send email itself.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List

from langgraph.types import interrupt

from . import store
from .config import get_settings
from .mail import templates
from .mail.receive import review_token
from .mail.send import OutgoingEmail, send


def run_dir(run_id: str) -> Path:
    d = get_settings().runs_dir / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:max_len].strip("-") or "untitled"


def send_review(state: dict, subject: str, html: str, text: str, status: str,
                attachments: List[Path] | None = None, inline: Dict[str, Path] | None = None) -> dict:
    seq = int(state.get("review_seq") or 0) + 1
    run_id = state["run_id"]
    send(OutgoingEmail(run_id, f"{review_token(run_id, seq)} {subject}", html, text, attachments or [], inline or {}))
    store.set_status(run_id, status)
    return {"review_seq": seq}


def send_notice(run_id: str, subject: str, html: str, text: str,
                attachments: List[Path] | None = None, inline: Dict[str, Path] | None = None) -> None:
    send(OutgoingEmail(run_id, f"[CA-{run_id}] {subject}", html, text, attachments or [], inline or {}))


def await_reply(stage: str) -> Callable[[dict], dict]:
    def node(state: dict) -> dict:
        reply = interrupt(
            {"stage": stage, "seq": state.get("review_seq", 0), "n_options": len(state.get("topic_options") or [])}
        )
        return {"reply": reply}

    node.__name__ = f"await_{stage}"
    return node


def send_clarify(stage: str) -> Callable[[dict], dict]:
    def node(state: dict) -> dict:
        html, text = templates.clarify_email(stage, (state.get("reply") or {}).get("feedback", ""))
        return send_review(state, "Quick question about your reply", html, text,
                           "waiting_topic" if stage == "topics" else "waiting_draft")

    node.__name__ = f"clarify_{stage}"
    return node


def end_run(state: dict, reason: str, attachments: List[Path] | None = None) -> dict:
    html, text = templates.ended_email(reason, state.get("title", ""))
    send_notice(state["run_id"], "Run ended", html, text, attachments)
    store.set_status(state["run_id"], "ended")
    return {"end_reason": reason}


def topic_decision(state: dict) -> dict:
    """Common handling of a topic-stage reply -> {'next': ..., counters}."""
    s = get_settings()
    reply = state.get("reply") or {}
    action = reply.get("action")
    retries = int(state.get("topic_retries") or 0)
    if action == "choose":
        return {"next": "chosen"}
    if action == "stop":
        return {"next": "end", "end_reason": "Stopped by reviewer at topic selection."}
    if action == "regenerate":
        if retries >= s.max_retries:
            return {"next": "end", "end_reason": f"No topic approved after {s.max_retries} regenerations."}
        return {"next": "regenerate", "topic_retries": retries + 1, "topic_feedback": reply.get("feedback", "")}
    return {"next": "clarify"}


def draft_decision(state: dict) -> dict:
    s = get_settings()
    reply = state.get("reply") or {}
    action = reply.get("action")
    retries = int(state.get("draft_retries") or 0)
    if action == "approve":
        return {"next": "approve"}
    if action == "stop":
        return {"next": "end", "end_reason": "Stopped by reviewer at draft review."}
    if action in ("revise", "regenerate"):
        if retries >= s.max_retries:
            return {"next": "end", "end_reason": f"Draft not approved after {s.max_retries} rewrites."}
        return {"next": action, "draft_retries": retries + 1, "feedback": reply.get("feedback", "")}
    return {"next": "clarify"}  # bare REJECT or unclear -> ask revise vs regenerate (not counted)
