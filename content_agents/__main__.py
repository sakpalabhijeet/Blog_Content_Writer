"""CLI:  python -m content_agents <command>

  start-blog      weekly AI research -> email 3 blog topics
  start-youtube   daily curious-topic research -> email 3 video ideas
  poll            check email for replies and continue waiting runs (schedule every 10 min)
  retry RUN_ID    continue a run that stopped on an error
  status          list recent runs
  reply RUN_ID "TEXT" [--attach FILE ...]   (MAIL_BACKEND=file only) simulate an email reply
  check           verify Ollama, models, sources, email and image providers
  serve           long-running scheduler (Docker): BLOG_SCHEDULE / YT_SCHEDULE / POLL_MINUTES
  schedule-info   print the .env schedules as JSON (used by scripts/register_tasks.ps1)

  --scheduled on start-* skips the start if that agent already started a run today.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import get_settings


def setup_logging(verbose: bool = False) -> None:
    s = get_settings()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(s.logs_dir / "agents.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "primp", "ddgs", "urllib3", "trafilatura", "charset_normalizer", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_status() -> None:
    from . import store

    rows = store.list_runs(30)
    if not rows:
        print("no runs yet")
    for r in rows:
        print(f"{r['created_at']}  {r['run_id']:<32} {r['status']:<14} {r['title']}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="content_agents", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("start-blog", "start-youtube"):
        sp = sub.add_parser(name)
        sp.add_argument("--days", type=int, default=None, help="research window in days")
        sp.add_argument("--scheduled", action="store_true", help="skip if this agent already started a run today")
    sub.add_parser("poll")
    sub.add_parser("serve")
    sub.add_parser("schedule-info")
    sp = sub.add_parser("retry")
    sp.add_argument("run_id")
    sub.add_parser("status")
    sp = sub.add_parser("reply")
    sp.add_argument("run_id")
    sp.add_argument("text")
    sp.add_argument("--attach", nargs="*", default=[])
    sp = sub.add_parser("check")
    sp.add_argument("--skip-sources", action="store_true")
    sp.add_argument("--skip-llm", action="store_true")
    args = p.parse_args(argv)
    setup_logging(args.verbose)

    from . import runner

    if args.cmd in ("start-blog", "start-youtube"):
        agent = "blog" if args.cmd == "start-blog" else "youtube"
        run_id = runner.start(agent, args.days, scheduled=args.scheduled)
        print(run_id or f"skipped: {agent} already started a run today")
    elif args.cmd == "poll":
        print(f"resumed {runner.poll()} run(s)")
    elif args.cmd == "serve":
        from .scheduler import serve

        serve()
    elif args.cmd == "schedule-info":
        from .scheduler import schedule_info

        try:
            print(json.dumps(schedule_info()))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif args.cmd == "retry":
        runner.retry(args.run_id)
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "reply":
        if get_settings().mail_backend != "file":
            raise SystemExit("`reply` only works with MAIL_BACKEND=file; with Gmail, reply to the email.")
        print(runner.file_reply(args.run_id, args.text, args.attach))
    elif args.cmd == "check":
        from .check import run_checks

        return run_checks(skip_sources=args.skip_sources, skip_llm=args.skip_llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
