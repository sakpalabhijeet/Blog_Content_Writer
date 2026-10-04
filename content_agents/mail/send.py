"""Outgoing mail: Gmail SMTP (app password) or file backend (writes .eml to data/outbox)."""
from __future__ import annotations

import logging
import mimetypes
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Dict, List

from .. import store
from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass
class OutgoingEmail:
    run_id: str
    subject: str
    html: str
    text: str
    attachments: List[Path] = field(default_factory=list)
    inline_images: Dict[str, Path] = field(default_factory=dict)  # cid -> file


def _mime(path: Path) -> tuple[str, str]:
    ctype, _ = mimetypes.guess_type(path.name)
    maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
    return maintype, subtype


def build_message(mail: OutgoingEmail) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["Subject"] = mail.subject
    msg["From"] = formataddr((s.sender_name, s.smtp_user or "agent@localhost"))
    msg["To"] = s.approver_email or "approver@localhost"
    domain = (s.smtp_user.split("@")[-1] if "@" in s.smtp_user else "localhost")
    msg["Message-ID"] = make_msgid(domain=domain)
    first = store.thread_msgid(mail.run_id)
    if first:  # keep every email of a run in one Gmail thread
        msg["In-Reply-To"] = first
        msg["References"] = first

    msg.set_content(mail.text)
    msg.add_alternative(mail.html, subtype="html")
    html_part = msg.get_payload()[-1]
    for cid, path in mail.inline_images.items():
        if path.exists():
            maintype, subtype = _mime(path)
            html_part.add_related(path.read_bytes(), maintype, subtype, cid=f"<{cid}>", filename=path.name)
    for path in mail.attachments:
        if path.exists():
            maintype, subtype = _mime(path)
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def send(mail: OutgoingEmail) -> str:
    """Send and return the Message-ID."""
    s = get_settings()
    msg = build_message(mail)
    if s.mail_backend == "gmail":
        if not (s.smtp_user and s.smtp_app_password and s.approver_email):
            raise RuntimeError("Set SMTP_USER, SMTP_APP_PASSWORD and APPROVER_EMAIL in .env for MAIL_BACKEND=gmail")
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=60) as smtp:
            smtp.starttls()
            smtp.login(s.smtp_user, s.smtp_app_password)
            smtp.send_message(msg)
        log.info("emailed %s: %s", s.approver_email, mail.subject)
    else:
        out = s.outbox_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{mail.run_id}.eml"
        out.write_bytes(bytes(msg))
        log.info("wrote %s (%s)", out, mail.subject)
    store.record_sent(msg["Message-ID"], mail.run_id, mail.subject)
    return msg["Message-ID"]
