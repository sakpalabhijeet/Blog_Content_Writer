"""Incoming approval replies: Gmail IMAP or file backend (data/inbox/<token>.txt).

Only replies FROM the approver whose subject carries the exact review token are accepted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import List, Optional, Tuple

from .. import store
from ..config import get_settings

log = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MIN_ATTACHMENT_BYTES = 10_000


def review_token(run_id: str, seq: int) -> str:
    return f"[CA-{run_id}#{seq}]"


@dataclass
class IncomingReply:
    uid: str
    text: str
    from_addr: str
    subject: str
    attachments: List[Tuple[str, bytes]] = field(default_factory=list)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "image"


def save_attachments(reply: IncomingReply, dest: Path) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, data in reply.attachments:
        if Path(name).suffix.lower() in IMAGE_EXT:
            p = dest / _safe_name(name)
            p.write_bytes(data)
            saved.append(str(p))
    return saved


# ---------------- Gmail ----------------
def _gmail_fetch(token: str) -> List[IncomingReply]:
    from imap_tools import AND, MailBox

    s = get_settings()
    out: List[IncomingReply] = []
    with MailBox(s.imap_host).login(s.smtp_user, s.smtp_app_password, "INBOX") as box:
        for m in box.fetch(AND(seen=False, from_=s.approver_email, subject=token), mark_seen=False):
            sender = parseaddr(m.from_)[1].lower()
            msg_id = (m.headers.get("message-id") or ("",))[0].strip()
            if sender != s.approver_email or token not in (m.subject or ""):
                continue
            if msg_id and store.is_our_message(msg_id):  # our own email when sender == approver
                continue
            out.append(
                IncomingReply(
                    uid=m.uid,
                    text=m.text or re.sub(r"<[^>]+>", " ", m.html or ""),
                    from_addr=sender,
                    subject=m.subject,
                    # Skip inline parts (signature logos) and tiny images; keep real attachments.
                    attachments=[(a.filename or "image", a.payload) for a in m.attachments
                                 if a.content_disposition != "inline" and a.size >= MIN_ATTACHMENT_BYTES],
                )
            )
    return out


def _gmail_mark_seen(uids: List[str]) -> None:
    from imap_tools import MailBox, MailMessageFlags

    s = get_settings()
    with MailBox(s.imap_host).login(s.smtp_user, s.smtp_app_password, "INBOX") as box:
        box.flag(uids, MailMessageFlags.SEEN, True)


# ---------------- File backend ----------------
def _file_key(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", token.strip("[]"))


def inbox_path(token: str) -> Path:
    return get_settings().inbox_dir / f"{_file_key(token)}.txt"


def _file_fetch(token: str) -> List[IncomingReply]:
    p = inbox_path(token)
    if not p.exists():
        return []
    att_dir = p.with_suffix("")
    attachments = [(f.name, f.read_bytes()) for f in sorted(att_dir.glob("*"))] if att_dir.is_dir() else []
    return [IncomingReply(uid=str(p), text=p.read_text(encoding="utf-8"), from_addr="file", subject=token, attachments=attachments)]


def _file_mark_seen(uids: List[str]) -> None:
    stamp = f"{datetime.now():%Y%m%d%H%M%S}"
    for uid in uids:
        p = Path(uid)
        if p.exists():
            p.rename(p.with_suffix(f".done-{stamp}"))


# ---------------- Public API ----------------
def fetch_latest_reply(token: str) -> Optional[IncomingReply]:
    """Newest matching reply (older duplicates are marked seen too)."""
    backend = get_settings().mail_backend
    replies = _gmail_fetch(token) if backend == "gmail" else _file_fetch(token)
    if not replies:
        return None
    if len(replies) > 1:
        mark_seen(replies[:-1])
    return replies[-1]


def mark_seen(replies: List[IncomingReply]) -> None:
    uids = [r.uid for r in replies]
    if not uids:
        return
    if get_settings().mail_backend == "gmail":
        _gmail_mark_seen(uids)
    else:
        _file_mark_seen(uids)
