"""End-to-end graph flows with the file mail backend and a fake LLM."""
from __future__ import annotations

import email
from email import policy
from pathlib import Path

from content_agents import runner, store
from content_agents.config import get_settings
from content_agents.text_utils import word_count


def outbox() -> list[Path]:
    return sorted(get_settings().outbox_dir.glob("*.eml"))


def last_subject() -> str:
    msg = email.message_from_bytes(outbox()[-1].read_bytes(), policy=policy.default)
    return msg["Subject"]


def reply(run_id: str, text: str) -> None:
    runner.file_reply(run_id, text)
    assert runner.poll() == 1


def test_blog_full_flow(fakes):
    run_id = runner.start("blog")
    assert store.get_run(run_id)["status"] == "waiting_topic"
    assert len(outbox()) == 1 and f"[CA-{run_id}#1]" in last_subject()

    reply(run_id, "2\nIMAGES: AUTO\n\nOn Mon, Sep 22, 2026 at 8:00 AM Agent <a@b.c> wrote:\n> 1. old")
    assert store.get_run(run_id)["status"] == "waiting_draft"
    assert "Blog draft v1" in last_subject()
    draft = Path(get_settings().runs_dir, run_id, "draft_v1.md").read_text(encoding="utf-8")
    assert word_count(draft) >= 2500  # enforce_length expanded the short sections
    assert draft.count("\n## ") >= 5
    assert "Suggested image" in draft  # no image providers configured -> suggestion blocks

    reply(run_id, "REVISE: make section 2 more practical")
    assert "Blog draft v2" in last_subject()
    v2 = Path(get_settings().runs_dir, run_id, "draft_v2.md").read_text(encoding="utf-8")
    assert "REVISED" in v2

    reply(run_id, "APPROVE")
    assert store.get_run(run_id)["status"] == "done"
    assert "Final blog post" in last_subject()
    final = Path(get_settings().runs_dir, run_id, "final-test-blog.md").read_text(encoding="utf-8")
    assert final.startswith("# Final Test Blog") and "TL;DR" in final
    assert "Final Test Blog" in store.recent_topics("blog", 1)


def test_topic_regenerate_until_max_retries(fakes):
    run_id = runner.start("blog")
    for i in range(3):
        reply(run_id, f"REGENERATE: something else {i}")
        assert store.get_run(run_id)["status"] == "waiting_topic"
    assert len(outbox()) == 4
    reply(run_id, "REGENERATE: again")
    assert store.get_run(run_id)["status"] == "ended"
    assert "Run ended" in last_subject()


def test_unclear_reply_asks_without_consuming_retry(fakes):
    run_id = runner.start("youtube")
    reply(run_id, "1\nLANG: HINDI")
    assert "YouTube script v1" in last_subject()

    reply(run_id, "REJECT")  # bare reject -> clarify email, same draft
    assert "Quick question" in last_subject()
    assert store.get_run(run_id)["status"] == "waiting_draft"

    reply(run_id, "REGENERATE: more suspense")
    assert "YouTube script v2" in last_subject()
    reply(run_id, "APPROVE")
    assert store.get_run(run_id)["status"] == "done"
    rd = Path(get_settings().runs_dir, run_id)
    voice = next(rd.glob("*_voiceover.txt")).read_text(encoding="utf-8")
    assert "[VISUAL" not in voice and "रहस्य" in voice


def test_draft_max_retries(fakes):
    run_id = runner.start("youtube")
    reply(run_id, "3")
    for i in range(3):
        reply(run_id, f"REVISE: fix {i}")
    assert "YouTube script v4" in last_subject()
    reply(run_id, "REVISE: once more")
    assert store.get_run(run_id)["status"] == "ended"


def test_reply_to_old_email_is_ignored(fakes):
    run_id = runner.start("blog")
    token_old = f"CA-{run_id}_1"
    runner.file_reply(run_id, "REGENERATE")
    runner.poll()  # now waiting on seq 2
    (get_settings().inbox_dir / f"{token_old}.txt").write_text("1", encoding="utf-8")
    assert runner.poll() == 0
    assert store.get_run(run_id)["status"] == "waiting_topic"
