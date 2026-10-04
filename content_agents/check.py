"""`python -m content_agents check` — environment diagnostics."""
from __future__ import annotations

import time

import httpx
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from . import llm as L
from .config import get_settings
from .images import providers
from .research import collect


class _Ping(BaseModel):
    ok: bool
    capital_of_india: str


def _line(ok: bool | None, label: str, detail: str = "") -> None:
    mark = {True: "OK  ", False: "FAIL", None: "WARN"}[ok]
    print(f"[{mark}] {label}{(' — ' + detail) if detail else ''}")


def run_checks(skip_sources: bool = False, skip_llm: bool = False) -> int:
    s = get_settings()
    failures = 0

    print("== Ollama ==")
    try:
        tags = httpx.get(f"{s.ollama_base_url}/api/tags", timeout=10).json()
        installed = {m["name"] for m in tags.get("models", [])}
        _line(True, f"Ollama reachable at {s.ollama_base_url}", f"{len(installed)} models")
        for role in ("writer", "fast", "script"):
            name = L.model_for(role)
            present = name in installed or f"{name}:latest" in installed
            _line(present, f"{role} model {name}", "" if present else f"run: ollama pull {name}")
            failures += not present
        if not skip_llm and not failures:
            t = time.time()
            out = L.structured(_Ping, [HumanMessage(content="Reply with ok=true and the capital of India.")], role="fast")
            _line(out.ok, "structured output (fast model)", f"{out.capital_of_india!r} in {time.time() - t:.0f}s")
    except Exception as e:
        failures += 1
        _line(False, "Ollama", f"{e} (is the Ollama app running?)")

    print("\n== Email ==")
    if s.mail_backend == "gmail":
        try:
            import smtplib

            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(s.smtp_user, s.smtp_app_password)
            _line(True, f"SMTP login {s.smtp_user}")
            from imap_tools import MailBox

            with MailBox(s.imap_host).login(s.smtp_user, s.smtp_app_password, "INBOX"):
                pass
            _line(True, "IMAP login")
            _line(bool(s.approver_email), "APPROVER_EMAIL", s.approver_email or "not set")
            failures += not s.approver_email
        except Exception as e:
            failures += 1
            _line(False, "Gmail", f"{e} (use an App Password and enable IMAP)")
    else:
        _line(None, "MAIL_BACKEND=file", f"emails -> {s.outbox_dir}, replies <- {s.inbox_dir}")

    print("\n== Images ==")
    for name, ok in providers.available().items():
        _line(True if ok else None, name, "available" if ok else "not configured (skipped)")

    if not skip_sources:
        for agent in ("blog", "youtube"):
            print(f"\n== Sources: {agent} ==")
            results = collect.run_jobs(collect.build_jobs(agent, 7))
            for label, items in results.items():
                _line(True if items else None, label, f"{len(items)} items")

    print("\nAll good." if not failures else f"\n{failures} problem(s) found.")
    return 1 if failures else 0
