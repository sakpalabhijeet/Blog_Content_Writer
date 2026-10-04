"""Start runs, poll email for approvals, and resume paused LangGraph threads."""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from . import hitl, store
from .blog import graph as blog_graph
from .config import get_settings
from .mail import receive, templates
from .mail.parse import parse_reply
from .youtube import graph as yt_graph

log = logging.getLogger(__name__)

BUILDERS = {"blog": blog_graph.build_graph, "youtube": yt_graph.build_graph}
LOCK_STALE_SECONDS = 4 * 3600


class LockBusy(RuntimeError):
    pass


@contextmanager
def run_lock(wait: bool, max_wait_seconds: int = 90 * 60) -> Iterator[None]:
    """One agent process at a time: the local LLM can only serve one heavy job on this machine."""
    path = get_settings().data_dir / "agent.lock"
    deadline = time.time() + max_wait_seconds
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS:
                log.warning("removing stale lock %s", path)
                path.unlink(missing_ok=True)
                continue
            if not wait or time.time() > deadline:
                raise LockBusy("another agent process is running")
            time.sleep(30)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def open_graph(agent: str):
    conn = sqlite3.connect(get_settings().checkpoint_db, check_same_thread=False)
    try:
        yield BUILDERS[agent](SqliteSaver(conn))
    finally:
        conn.close()


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "max_concurrency": get_settings().llm_concurrency,
            "recursion_limit": 250}


def _drive(graph, run_id: str, inp) -> None:
    try:
        graph.invoke(inp, _config(run_id))
    except Exception as e:
        log.exception("run %s failed", run_id)
        store.set_status(run_id, "error")
        try:
            html, text = templates.ended_email(
                f"The agent hit an error and paused: {e}. Fix the cause (e.g. start Ollama) and run "
                f"`python -m content_agents retry {run_id}` to continue from where it stopped."
            )
            hitl.send_notice(run_id, "Agent error", html, text)
        except Exception:
            log.exception("could not send error email")
        raise


def new_run_id(agent: str) -> str:
    return f"{agent}-{datetime.now():%Y%m%d-%H%M}-{secrets.token_hex(2)}"


def start(agent: str, recency_days: Optional[int] = None, scheduled: bool = False) -> Optional[str]:
    """Start a run and return its id. A scheduled start returns None if this agent already started a run today
    (Task Scheduler, `serve` or a manual start), so two schedulers never email duplicate topics."""
    s = get_settings()
    run_id = new_run_id(agent)
    days = recency_days or (s.blog_recency_days if agent == "blog" else s.yt_recency_days)
    initial = {"run_id": run_id, "as_of": date.today().isoformat(), "recency_days": days,
               "topic_retries": 0, "draft_retries": 0, "review_seq": 0, "draft_version": 0}
    if agent == "blog":
        initial["sections"] = []
    with run_lock(wait=True):
        # Checked under the lock so two schedulers firing at once can't both pass.
        if scheduled and store.started_on(agent, date.today().isoformat()):
            log.info("scheduled %s skipped: already started a run today", agent)
            return None
        store.create_run(run_id, agent)
        log.info("starting %s", run_id)
        with open_graph(agent) as graph:
            _drive(graph, run_id, initial)
    return run_id


def pending_interrupt(graph, run_id: str) -> Optional[dict]:
    snap = graph.get_state(_config(run_id))
    return snap.interrupts[0].value if snap.interrupts else None


def poll() -> int:
    """Check email for replies to every waiting run; resume those that got one. Returns runs resumed."""
    try:
        lock = run_lock(wait=False)
        lock.__enter__()
    except LockBusy:
        log.info("poll skipped: another agent process is running")
        return 0
    resumed = 0
    try:
        for run in store.list_waiting():
            run_id, agent = run["run_id"], run["agent"]
            with open_graph(agent) as graph:
                payload = pending_interrupt(graph, run_id)
                if not payload:
                    continue
                token = receive.review_token(run_id, payload["seq"])
                msg = receive.fetch_latest_reply(token)
                if not msg:
                    continue
                reply = parse_reply(msg.text, payload["stage"], payload.get("n_options") or 3)
                reply.attachments = receive.save_attachments(msg, hitl.run_dir(run_id) / "user_images")
                log.info("run %s: reply %s (choice=%s)", run_id, reply.action, reply.choice)
                receive.mark_seen([msg])  # before resuming: a crash must not replay the same reply
                store.set_status(run_id, "running")
                try:
                    _drive(graph, run_id, Command(resume=reply.model_dump()))
                except Exception:
                    continue
                resumed += 1
    finally:
        lock.__exit__(None, None, None)
    return resumed


def retry(run_id: str) -> None:
    """Continue a run that stopped on an error (from its last checkpoint)."""
    run = store.get_run(run_id)
    if not run:
        raise SystemExit(f"unknown run {run_id}")
    with run_lock(wait=True), open_graph(run["agent"]) as graph:
        payload = pending_interrupt(graph, run_id)
        if payload:
            store.set_status(run_id, "waiting_draft" if payload["stage"] == "draft" else "waiting_topic")
            log.info("run %s is waiting for a reply; nothing to retry", run_id)
            return
        store.set_status(run_id, "running")
        _drive(graph, run_id, None)


def clear_lock() -> None:
    (get_settings().data_dir / "agent.lock").unlink(missing_ok=True)


def resume_interrupted() -> None:
    """Continue runs that were mid-step when the process stopped (e.g. container restart)."""
    for run in store.list_by_status("running"):
        run_id = run["run_id"]
        with open_graph(run["agent"]) as graph:
            snap = graph.get_state(_config(run_id))
            if snap.interrupts:  # actually waiting for a reply; fix the status
                stage = snap.interrupts[0].value.get("stage")
                store.set_status(run_id, "waiting_draft" if stage == "draft" else "waiting_topic")
                continue
            if not snap.next:  # finished, or never got a first checkpoint
                continue
        log.info("resuming interrupted run %s at %s", run_id, snap.next)
        try:
            retry(run_id)
        except Exception:
            log.exception("could not resume %s", run_id)


def file_reply(run_id: str, text: str, attach: list[str] | None = None) -> str:
    """MAIL_BACKEND=file helper: drop a reply for the run's current review step into data/inbox."""
    import shutil

    run = store.get_run(run_id)
    if not run:
        raise SystemExit(f"unknown run {run_id}")
    with open_graph(run["agent"]) as graph:
        payload = pending_interrupt(graph, run_id)
    if not payload:
        raise SystemExit(f"run {run_id} is not waiting for a reply")
    token = receive.review_token(run_id, payload["seq"])
    path = receive.inbox_path(token)
    path.write_text(text, encoding="utf-8")
    if attach:
        d = path.with_suffix("")
        d.mkdir(exist_ok=True)
        for a in attach:
            shutil.copy(a, d)
    return str(path)
