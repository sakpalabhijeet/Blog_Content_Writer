from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit

import yaml

from . import fetchers

log = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).resolve().parent.parent / "sources.yaml"


@lru_cache
def load_sources() -> dict:
    return yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8")) or {}


def _norm_url(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", ""))


def dedupe(items: List[dict]) -> List[dict]:
    seen, out = set(), []
    for it in items:
        if not it.get("url") or not it.get("title"):
            continue
        key = _norm_url(it["url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _reddit_sequential(subs: List[str], period: str, per_sub: int = 4) -> List[dict]:
    out: List[dict] = []
    for i, sub in enumerate(subs):
        if i:
            time.sleep(2)
        out.extend(fetchers.reddit(sub, period)[:per_sub])
    return out


Job = Tuple[str, Callable[[], List[dict]], int]  # (label, fetch-callable, recency window in days)


def build_jobs(agent: str, since_days: int) -> List[Job]:
    """Fetch jobs for every configured source of an agent. RSS feeds and DDG queries may set their own `days`
    (e.g. evergreen personal-finance sources need a wider window than breaking news)."""
    cfg = load_sources().get(agent, {})
    year = str(date.today().year)
    since_dt = datetime.now() - timedelta(days=since_days)
    since_ts, since_date = int(since_dt.timestamp()), since_dt.strftime("%Y-%m-%d")
    period = "day" if since_days <= 1 else "week" if since_days <= 7 else "month"

    jobs: List[Job] = []
    for f in cfg.get("rss", []):
        jobs.append((f["name"], lambda f=f: fetchers.rss(f["url"], f["name"]), int(f.get("days", since_days))))
    if cfg.get("hf_daily_papers"):
        jobs.append(("HF Daily Papers", fetchers.hf_daily_papers, since_days))
    hn_cfg = cfg.get("hn") or {}
    for q in hn_cfg.get("queries", []):
        jobs.append((f"HN: {q}", lambda q=q: fetchers.hn(q, since_ts, hn_cfg.get("min_points", 80)), since_days))
    for q in (cfg.get("github") or {}).get("queries", []):
        jobs.append((f"GitHub: {q}", lambda q=q: fetchers.github_repos(q, since_date), since_days))
    subs = cfg.get("reddit", [])
    if subs:
        # One sequential job: Reddit rate-limits parallel unauthenticated requests.
        jobs.append((f"Reddit ({len(subs)} subs)", lambda: _reddit_sequential(subs, period), since_days))
    for entry in cfg.get("ddg_news", []):
        q, days = (entry["q"], int(entry.get("days", since_days))) if isinstance(entry, dict) else (entry, since_days)
        q = q.replace("{year}", year)
        jobs.append((f"DDG: {q}", lambda q=q, days=days: fetchers.ddg_news(q, days), days))
    return jobs


def run_jobs(jobs: List[Job]) -> Dict[str, List[dict]]:
    results: Dict[str, List[dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {label: pool.submit(fn) for label, fn, _ in jobs}
        for label, fut in futures.items():
            try:
                results[label] = fut.result() or []
            except Exception as e:  # fetchers shouldn't raise, but be safe
                log.warning("%s failed: %s", label, e)
                results[label] = []
    return results


def collect(agent: str, since_days: int, max_items: int = 60, per_source_cap: int = 6) -> List[dict]:
    """Fetch all sources for an agent, keep recent items, dedupe, and balance across sources."""
    jobs = build_jobs(agent, since_days)
    windows = {label: days for label, _, days in jobs}
    per_label = run_jobs(jobs)

    buckets: List[List[dict]] = []
    for label, items in per_label.items():
        # Undated items are kept (feeds are newest-first); dated items must be inside the source's window.
        cutoff = (date.today() - timedelta(days=windows[label])).isoformat()
        recent = [it for it in items if not it.get("published_at") or it["published_at"] >= cutoff]
        recent.sort(key=lambda it: it.get("published_at") or "", reverse=True)
        cap = per_source_cap * 2 if label.startswith("Reddit") else per_source_cap  # many subs in one job
        buckets.append(recent[:cap])
        log.info("source %-40s %d items (%d recent)", label, len(items), len(recent))

    # Round-robin across sources so one noisy feed can't dominate.
    merged: List[dict] = []
    for i in range(per_source_cap * 2):
        for b in buckets:
            if i < len(b):
                merged.append(b[i])
    return dedupe(merged)[:max_items]


def format_digest(items: List[dict], snippet_chars: int = 220) -> str:
    """Numbered, compact list for LLM prompts. The LLM refers to items by number, never copies URLs."""
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f"[{i}] {it['title']} ({it.get('source') or 'web'}, {it.get('published_at') or 'date unknown'})"
            f" — {(it.get('snippet') or '')[:snippet_chars]}"
        )
    return "\n".join(lines)
