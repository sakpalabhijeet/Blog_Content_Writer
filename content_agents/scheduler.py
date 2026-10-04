"""Built-in scheduler for `python -m content_agents serve` (Docker has no Windows Task Scheduler).

Schedules look like "daily 07:00", "mon 08:00", "mon,thu 09:30" or "off". A job runs once per matching day,
at or after its time, so a machine that was off at 08:00 catches up when it starts (like "run when available").
"""
from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from typing import Optional, Set

from . import runner, store
from .config import get_settings

log = logging.getLogger(__name__)

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass(frozen=True)
class Schedule:
    days: Optional[Set[int]]  # None = every day
    at: dtime


def parse_schedule(spec: str) -> Optional[Schedule]:
    spec = (spec or "").strip().lower()
    if not spec or spec in ("off", "none", "disabled"):
        return None
    try:
        day_part, time_part = spec.split()
        hh, mm = (int(x) for x in time_part.split(":"))
        days = None if day_part in ("daily", "everyday", "*") else {_DAYS[d.strip()[:3]] for d in day_part.split(",")}
        return Schedule(days, dtime(hh, mm))
    except (ValueError, KeyError) as e:
        raise ValueError(f"bad schedule {spec!r}; use e.g. 'daily 07:00', 'mon 08:00', 'mon,thu 09:30' or 'off'") from e


def is_due(schedule: Optional[Schedule], now: datetime, last_date: Optional[str]) -> bool:
    if schedule is None:
        return False
    if schedule.days is not None and now.weekday() not in schedule.days:
        return False
    return now.time() >= schedule.at and last_date != now.date().isoformat()


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def schedule_info() -> dict:
    """The .env schedules in a form scripts/register_tasks.ps1 can turn into Task Scheduler triggers."""
    s = get_settings()

    def describe(spec: str) -> Optional[dict]:
        sched = parse_schedule(spec)
        if sched is None:
            return None
        days = None if sched.days is None else [_DAY_NAMES[d] for d in sorted(sched.days)]
        return {"days": days, "time": sched.at.strftime("%H:%M")}

    return {"blog": describe(s.blog_schedule), "youtube": describe(s.yt_schedule), "poll_minutes": s.poll_minutes}


_stop = False


def _handle_stop(signum, frame):  # docker stop sends SIGTERM
    global _stop
    _stop = True
    log.info("stop requested; finishing the current step")


def serve() -> None:
    s = get_settings()
    jobs = {"blog": parse_schedule(s.blog_schedule), "youtube": parse_schedule(s.yt_schedule)}
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    runner.clear_lock()  # a previous container may have died holding it
    runner.resume_interrupted()
    log.info("serve: blog=%r youtube=%r poll every %d min (local time %s)",
             s.blog_schedule, s.yt_schedule, s.poll_minutes, datetime.now().strftime("%Y-%m-%d %H:%M"))

    last_poll = 0.0
    while not _stop:
        now = datetime.now()
        for agent, sched in jobs.items():
            if _stop or not is_due(sched, now, store.last_scheduled(agent)):
                continue
            store.mark_scheduled(agent, now.date().isoformat())  # mark first: a crash must not re-run it all day
            try:
                run_id = runner.start(agent, scheduled=True)
                if run_id:
                    log.info("scheduled start: %s", run_id)
            except Exception:
                log.exception("scheduled %s run failed", agent)
        if not _stop and time.time() - last_poll >= s.poll_minutes * 60:
            last_poll = time.time()
            try:
                n = runner.poll()
                if n:
                    log.info("poll resumed %d run(s)", n)
            except Exception:
                log.exception("poll failed")
        for _ in range(30):  # sleep ~30s, waking quickly on stop
            if _stop:
                break
            time.sleep(1)
    log.info("serve stopped")
