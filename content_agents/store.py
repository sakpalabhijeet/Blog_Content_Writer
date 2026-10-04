"""Run registry, sent-message log and topic history (SQLite)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator, List, Optional

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT DEFAULT '',
    thread_msgid TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_messages (
    message_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    subject TEXT,
    sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_history (
    agent TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_runs (
    job TEXT PRIMARY KEY,
    last_date TEXT NOT NULL
);
"""

WAITING = ("waiting_topic", "waiting_draft")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(get_settings().state_db)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def create_run(run_id: str, agent: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO runs(run_id, agent, status, created_at, updated_at) VALUES (?,?,?,?,?)",
            (run_id, agent, "running", _now(), _now()),
        )


def set_status(run_id: str, status: str, title: Optional[str] = None) -> None:
    with _conn() as c:
        if title is not None:
            c.execute("UPDATE runs SET status=?, title=?, updated_at=? WHERE run_id=?", (status, title, _now(), run_id))
        else:
            c.execute("UPDATE runs SET status=?, updated_at=? WHERE run_id=?", (status, _now(), run_id))


def get_run(run_id: str) -> Optional[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()


def list_runs(limit: int = 20) -> List[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def started_on(agent: str, day: str) -> bool:
    """True if a run of this agent was created on `day` (YYYY-MM-DD)."""
    with _conn() as c:
        return c.execute(
            "SELECT 1 FROM runs WHERE agent=? AND created_at LIKE ? LIMIT 1", (agent, f"{day}%")
        ).fetchone() is not None


def list_by_status(status: str) -> List[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM runs WHERE status=? ORDER BY created_at", (status,)).fetchall()


def last_scheduled(job: str) -> Optional[str]:
    with _conn() as c:
        row = c.execute("SELECT last_date FROM schedule_runs WHERE job=?", (job,)).fetchone()
    return row["last_date"] if row else None


def mark_scheduled(job: str, day: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO schedule_runs(job, last_date) VALUES (?,?)", (job, day))


def list_waiting() -> List[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            f"SELECT * FROM runs WHERE status IN ({','.join('?' * len(WAITING))}) ORDER BY created_at",
            WAITING,
        ).fetchall()


def record_sent(message_id: str, run_id: str, subject: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO sent_messages(message_id, run_id, subject, sent_at) VALUES (?,?,?,?)",
            (message_id, run_id, subject, _now()),
        )
        c.execute(
            "UPDATE runs SET thread_msgid=? WHERE run_id=? AND (thread_msgid IS NULL OR thread_msgid='')",
            (message_id, run_id),
        )


def is_our_message(message_id: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM sent_messages WHERE message_id=?", (message_id,)).fetchone() is not None


def thread_msgid(run_id: str) -> str:
    row = get_run(run_id)
    return (row["thread_msgid"] if row else "") or ""


def add_topic_history(agent: str, title: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO topic_history(agent, title, created_at) VALUES (?,?,?)", (agent, title, _now()))


def recent_topics(agent: str, days: int) -> List[str]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with _conn() as c:
        rows = c.execute(
            "SELECT title FROM topic_history WHERE agent=? AND created_at>=? ORDER BY created_at DESC",
            (agent, cutoff),
        ).fetchall()
    return [r["title"] for r in rows]
