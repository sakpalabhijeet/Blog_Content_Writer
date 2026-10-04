"""Free, no-key source fetchers. Every fetcher returns normalized dicts:
{title, url, snippet, published_at (YYYY-MM-DD | None), source} and never raises.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import httpx

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) content-agents/1.0 (research bot)"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(text: Optional[str], limit: int = 400) -> str:
    text = _WS.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()
    return text[:limit]


def _item(title, url, snippet="", published_at=None, source=None) -> dict:
    return {
        "title": _clean(title, 200),
        "url": (url or "").strip(),
        "snippet": _clean(snippet),
        "published_at": published_at,
        "source": source,
    }


def _get(url: str, **kw) -> httpx.Response:
    headers = {"User-Agent": UA, **kw.pop("headers", {})}
    r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True, **kw)
    r.raise_for_status()
    return r


def _date_from_struct(st) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%d", st) if st else None
    except Exception:
        return None


def _date_from_ts(ts) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def rss(url: str, name: str, limit: int = 15) -> List[dict]:
    try:
        feed = feedparser.parse(_get(url).content)
        out = []
        for e in feed.entries[:limit]:
            published = _date_from_struct(e.get("published_parsed") or e.get("updated_parsed"))
            out.append(_item(e.get("title"), e.get("link"), e.get("summary"), published, name))
        return out
    except Exception as e:
        log.warning("rss %s failed: %s", name, e)
        return []


def hn(query: str, since_ts: int, min_points: int = 80, limit: int = 20) -> List[dict]:
    try:
        r = _get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>{since_ts},points>{min_points}",
                "hitsPerPage": limit,
            },
        )
        out = []
        for h in r.json().get("hits", []):
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            snippet = f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on Hacker News"
            out.append(_item(h.get("title"), url, snippet, _date_from_ts(h.get("created_at_i")), "Hacker News"))
        return out
    except Exception as e:
        log.warning("hn %r failed: %s", query, e)
        return []


def github_repos(query: str, since_date: str, limit: int = 10) -> List[dict]:
    try:
        r = _get(
            "https://api.github.com/search/repositories",
            params={"q": f"{query} created:>{since_date}", "sort": "stars", "order": "desc", "per_page": limit},
            headers={"Accept": "application/vnd.github+json"},
        )
        out = []
        for repo in r.json().get("items", []):
            snippet = f"★{repo.get('stargazers_count', 0)} · {repo.get('language') or ''} · {repo.get('description') or ''}"
            out.append(
                _item(repo.get("full_name"), repo.get("html_url"), snippet, (repo.get("created_at") or "")[:10] or None, "GitHub")
            )
        return out
    except Exception as e:
        log.warning("github %r failed: %s", query, e)
        return []


def reddit(sub: str, period: str = "week", limit: int = 15) -> List[dict]:
    try:
        r = _get(f"https://www.reddit.com/r/{sub}/top.json", params={"t": period, "limit": limit})
        out = []
        for child in r.json().get("data", {}).get("children", []):
            d = child.get("data", {})
            link = d.get("url_overridden_by_dest") or f"https://www.reddit.com{d.get('permalink', '')}"
            snippet = d.get("selftext") or f"{d.get('score', 0)} upvotes on r/{sub}"
            out.append(_item(d.get("title"), link, snippet, _date_from_ts(d.get("created_utc")), f"r/{sub}"))
        return out
    except Exception as e:
        log.info("reddit json r/%s failed (%s); trying RSS", sub, e)
        return rss(f"https://www.reddit.com/r/{sub}/top/.rss?t={period}", f"r/{sub}", limit)


def hf_daily_papers(limit: int = 20) -> List[dict]:
    try:
        r = _get("https://huggingface.co/api/daily_papers", params={"limit": limit})
        out = []
        for p in r.json():
            paper = p.get("paper", p)
            pid = paper.get("id")
            out.append(
                _item(
                    paper.get("title") or p.get("title"),
                    f"https://huggingface.co/papers/{pid}" if pid else "",
                    paper.get("summary") or p.get("summary"),
                    (p.get("publishedAt") or paper.get("publishedAt") or "")[:10] or None,
                    "HF Daily Papers",
                )
            )
        return out
    except Exception as e:
        log.warning("hf daily papers failed: %s", e)
        return []


def _ddg_timelimit(days: int) -> str:
    return "d" if days <= 1 else "w" if days <= 7 else "m" if days <= 31 else "y"


def ddg_news(query: str, days: int = 7, limit: int = 8) -> List[dict]:
    try:
        from ddgs import DDGS

        rows = DDGS().news(query, timelimit=_ddg_timelimit(days), max_results=limit) or []
        return [
            _item(r.get("title"), r.get("url"), r.get("body"), (r.get("date") or "")[:10] or None, r.get("source") or "Web")
            for r in rows
        ]
    except Exception as e:
        log.warning("ddg news %r failed: %s", query, e)
        return []


def ddg_text(query: str, limit: int = 8, days: Optional[int] = None) -> List[dict]:
    try:
        from ddgs import DDGS

        kw = {"timelimit": _ddg_timelimit(days)} if days else {}
        rows = DDGS().text(query, max_results=limit, **kw) or []
        return [_item(r.get("title"), r.get("href"), r.get("body"), None, "Web") for r in rows]
    except Exception as e:
        log.warning("ddg text %r failed: %s", query, e)
        return []


def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    try:
        import trafilatura

        text = trafilatura.extract(_get(url).text, include_comments=False, include_tables=False) or ""
        return _WS.sub(" ", text)[:max_chars]
    except Exception as e:
        log.info("page fetch %s failed: %s", url, e)
        return ""
