from datetime import datetime

import pytest

from content_agents import runner, store
from content_agents.scheduler import is_due, parse_schedule

MON_0900 = datetime(2026, 9, 21, 9, 0)  # a Monday


def test_parse_schedule():
    s = parse_schedule("mon,thu 08:30")
    assert s.days == {0, 3} and (s.at.hour, s.at.minute) == (8, 30)
    assert parse_schedule("daily 07:00").days is None
    assert parse_schedule("off") is None
    with pytest.raises(ValueError):
        parse_schedule("someday 7")


def test_is_due():
    weekly = parse_schedule("mon 08:00")
    assert is_due(weekly, MON_0900, None)
    assert not is_due(weekly, MON_0900, "2026-09-21")  # already ran today
    assert not is_due(weekly, MON_0900.replace(hour=7), None)  # too early
    assert not is_due(weekly, datetime(2026, 9, 22, 9, 0), None)  # Tuesday
    assert is_due(parse_schedule("daily 07:00"), datetime(2026, 9, 22, 23, 0), "2026-09-21")  # catch-up
    assert not is_due(None, MON_0900, None)


def test_resume_interrupted_fixes_waiting_status(fakes):
    run_id = runner.start("blog")
    store.set_status(run_id, "running")  # simulate a crash right after the email was sent
    runner.resume_interrupted()
    assert store.get_run(run_id)["status"] == "waiting_topic"


def test_scheduled_start_skips_when_already_started_today(fakes):
    from content_agents.config import get_settings

    first = runner.start("blog", scheduled=True)
    assert first
    assert runner.start("blog", scheduled=True) is None  # second scheduler / Task Scheduler + serve
    assert len(list(get_settings().outbox_dir.glob("*.eml"))) == 1
    assert runner.start("blog")  # manual start always runs
    assert runner.start("youtube", scheduled=True)  # other agent unaffected


def test_serve_starts_scheduled(monkeypatch):
    from content_agents import scheduler

    calls = []

    def fake_start(agent, recency_days=None, scheduled=False):
        calls.append((agent, scheduled))
        scheduler._stop = True  # end the loop after the first tick
        return None

    monkeypatch.setenv("BLOG_SCHEDULE", "daily 00:00")
    monkeypatch.setenv("YT_SCHEDULE", "off")
    from content_agents.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(runner, "start", fake_start)
    monkeypatch.setattr(runner, "poll", lambda: 0)
    monkeypatch.setattr(scheduler.signal, "signal", lambda *a: None)
    scheduler._stop = False
    scheduler.serve()
    scheduler._stop = False
    assert calls == [("blog", True)]


@pytest.mark.parametrize("blog,yt,expected_blog,expected_yt", [
    ("mon,thu 09:30", "daily 07:00", {"days": ["Monday", "Thursday"], "time": "09:30"}, {"days": None, "time": "07:00"}),
    ("off", "sat 18:05", None, {"days": ["Saturday"], "time": "18:05"}),
])
def test_schedule_info_cli(monkeypatch, capsys, blog, yt, expected_blog, expected_yt):
    import json

    from content_agents.__main__ import main
    from content_agents.config import get_settings

    monkeypatch.setenv("BLOG_SCHEDULE", blog)
    monkeypatch.setenv("YT_SCHEDULE", yt)
    monkeypatch.setenv("POLL_MINUTES", "5")
    get_settings.cache_clear()
    assert main(["schedule-info"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"blog": expected_blog, "youtube": expected_yt, "poll_minutes": 5}


def test_schedule_info_cli_rejects_bad_schedule(monkeypatch, capsys):
    from content_agents.__main__ import main
    from content_agents.config import get_settings

    monkeypatch.setenv("BLOG_SCHEDULE", "someday 7")
    get_settings.cache_clear()
    assert main(["schedule-info"]) == 2
    assert "bad schedule" in capsys.readouterr().err
