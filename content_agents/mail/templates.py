"""Email bodies (HTML + plain text) for every review step."""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Dict, List, Tuple

import markdown as md_lib

_WRAP = (
    '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:760px;margin:auto;color:#1f2328;'
    'line-height:1.6;font-size:15px">{body}</div>'
)
_BOX = (
    '<div style="background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:14px 18px;'
    'margin:16px 0;font-size:14px">{body}</div>'
)
_CODE_STYLE = "background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto;font-size:13px"


def md_to_html(md: str, base_dir: Path | None = None) -> Tuple[str, Dict[str, Path]]:
    """Render Markdown; local image refs become cid: links for inline embedding."""
    body = md_lib.markdown(md, extensions=["fenced_code", "tables", "sane_lists"])
    body = body.replace("<pre>", f'<pre style="{_CODE_STYLE}">')
    body = body.replace("<img ", '<img style="max-width:100%;height:auto;border-radius:6px" ')
    inline: Dict[str, Path] = {}
    if base_dir is not None:
        def swap(m: re.Match) -> str:
            src = m.group(1)
            if src.startswith(("http://", "https://", "cid:", "data:")):
                return m.group(0)
            path = (base_dir / src).resolve()
            if not path.exists():
                return m.group(0)
            cid = f"img{len(inline) + 1}.{re.sub(r'[^A-Za-z0-9]', '', path.stem)[:20]}@agent"
            inline[cid] = path
            return f'src="cid:{cid}"'

        body = re.sub(r'src="([^"]+)"', swap, body)
    return body, inline


def _instructions(lines: List[str]) -> Tuple[str, str]:
    html_lines = "".join(f"<li>{line}</li>" for line in lines)
    text = "\n".join(f"  - {html.unescape(re.sub(r'<[^>]+>', '', line))}" for line in lines)
    return _BOX.format(body=f"<b>How to reply</b> (just reply to this email):<ul>{html_lines}</ul>"), text


def topics_email(heading: str, intro: str, options: List[dict], agent: str, attempt: int, max_retries: int) -> Tuple[str, str]:
    cards, text_opts = [], []
    for i, o in enumerate(options, 1):
        fields = [(k, v) for k, v in o.items() if k not in ("title", "sources", "source_ids") and v]
        rows = "".join(
            f"<div><b>{html.escape(k.replace('_', ' ').title())}:</b> {html.escape(str(v))}</div>" for k, v in fields
        )
        srcs = "".join(
            f'<li><a href="{html.escape(s["url"])}">{html.escape(s["title"][:90])}</a></li>' for s in o.get("sources", [])[:4]
        )
        cards.append(
            f'<div style="border:1px solid #d0d7de;border-radius:8px;padding:14px 18px;margin:12px 0">'
            f'<div style="font-size:17px;font-weight:600">{i}. {html.escape(o["title"])}</div>{rows}'
            + (f'<div style="margin-top:6px"><b>Sources:</b><ul style="margin:4px 0">{srcs}</ul></div>' if srcs else "")
            + "</div>"
        )
        text_opts.append(f"{i}. {o['title']}\n" + "\n".join(f"   {k}: {v}" for k, v in fields))

    extra = (
        ["Optional line <code>IMAGES: AUTO</code> (default), <code>IMAGES: MANUAL</code> (attach your own images "
         "to the reply, or get suggested spots), or <code>IMAGES: NONE</code>"]
        if agent == "blog"
        else ["Optional line <code>LANG: HINDI</code> (default) or <code>LANG: ENGLISH</code>"]
    )
    box, box_text = _instructions(
        ["<b>1</b>, <b>2</b> or <b>3</b> to pick a topic (you can add notes after the number)"]
        + extra
        + ["<b>REGENERATE: &lt;what you want instead&gt;</b> for new suggestions",
           "<b>STOP</b> to skip this round"]
    )
    note = f"<p style='color:#57606a'>Suggestion round {attempt} of {max_retries + 1}.</p>"
    body = f"<h2>{html.escape(heading)}</h2><p>{html.escape(intro)}</p>{box}{''.join(cards)}{note}"
    text = f"{heading}\n\n{intro}\n\nHow to reply:\n{box_text}\n\n" + "\n\n".join(text_opts)
    return _WRAP.format(body=body), text


def draft_email(kind: str, title: str, content_html: str, content_md: str, stats: str, version: int,
                attempt: int, max_retries: int, notes: str = "") -> Tuple[str, str]:
    box, box_text = _instructions(
        ["<b>APPROVE</b> to finalize and receive the final version",
         "<b>REVISE: &lt;what to change&gt;</b> to re-evaluate and edit this draft",
         "<b>REGENERATE: &lt;what you want&gt;</b> to start over with a new outline",
         "<b>STOP</b> to cancel"]
        + (["Attach images to a REVISE reply to use your own images"] if kind == "blog" else [])
    )
    meta = (
        f"<p style='color:#57606a'>{html.escape(stats)} · Draft v{version} · "
        f"review round {attempt} of {max_retries + 1}</p>"
        + (f"<p style='color:#9a6700'>{html.escape(notes)}</p>" if notes else "")
    )
    body = f"<h2>Draft ready for review: {html.escape(title)}</h2>{meta}{box}<hr>{content_html}<hr>{box}"
    text = f"Draft ready for review: {title}\n{stats} · v{version}\n\nHow to reply:\n{box_text}\n\n{content_md}"
    return _WRAP.format(body=body), text


def clarify_email(stage: str, received: str) -> Tuple[str, str]:
    if stage == "draft":
        options = ["<b>REVISE: &lt;what to change&gt;</b> — keep this draft and fix the parts you mention",
                   "<b>REGENERATE: &lt;what you want&gt;</b> — start over with a fresh outline",
                   "<b>APPROVE</b> or <b>STOP</b>"]
        q = "Would you like me to re-evaluate (revise) this draft or regenerate it from scratch?"
    else:
        options = ["<b>1</b>, <b>2</b> or <b>3</b> to pick a topic", "<b>REGENERATE: &lt;notes&gt;</b>", "<b>STOP</b>"]
        q = "I couldn't tell which topic you picked."
    box, box_text = _instructions(options)
    quoted = html.escape(received[:600]) or "(empty)"
    body = f"<p>{q}</p>{box}<p style='color:#57606a'>Your reply was:</p><blockquote>{quoted}</blockquote>"
    return _WRAP.format(body=body), f"{q}\n\nReply with:\n{box_text}\n\nYour reply was:\n{received[:600]}"


def final_email(title: str, content_html: str, content_md: str, meta_html: str, meta_text: str) -> Tuple[str, str]:
    body = (f"<h2>✅ Final: {html.escape(title)}</h2>" + _BOX.format(body=meta_html)
            + "<p>The Markdown source and images are attached.</p><hr>" + content_html)
    return _WRAP.format(body=body), f"FINAL: {title}\n\n{meta_text}\n\n{content_md}"


def ended_email(reason: str, last_title: str = "") -> Tuple[str, str]:
    body = f"<p>{html.escape(reason)}</p>" + (f"<p>Last version: <b>{html.escape(last_title)}</b> (attached if available).</p>" if last_title else "")
    return _WRAP.format(body=body), reason
