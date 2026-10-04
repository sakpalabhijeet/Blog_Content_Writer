from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _str(name: str, default: str = "") -> str:
    value = (os.environ.get(name) or "").strip()
    if value.startswith("#"):  # "KEY=   # comment" is parsed by dotenv as the comment text
        value = ""
    return value or default


def _int(name: str, default: int) -> int:
    raw = _str(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # LLM (local Ollama)
    ollama_base_url: str
    writer_model: str
    fast_model: str
    script_model: str
    writer_ctx: int
    fast_ctx: int
    llm_timeout: int
    llm_concurrency: int

    # Mail
    mail_backend: str  # "gmail" | "file"
    smtp_host: str
    smtp_port: int
    imap_host: str
    smtp_user: str
    smtp_app_password: str
    approver_email: str
    sender_name: str

    # Images
    pexels_api_key: str
    unsplash_access_key: str
    google_api_key: str
    gemini_image_model: str

    # Workflow
    max_retries: int
    blog_min_words: int
    blog_min_sections: int
    blog_recency_days: int
    yt_recency_days: int
    yt_target_words: int
    script_language: str  # "hindi" | "english"

    # Built-in scheduler (`serve`, used in Docker)
    blog_schedule: str  # e.g. "mon 08:00", "mon,thu 09:30", "daily 07:00", "off"
    yt_schedule: str
    poll_minutes: int

    # Paths
    data_dir: Path

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.sqlite"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.runs_dir, self.outbox_dir, self.inbox_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(_str("DATA_DIR", str(ROOT / "data")))
    s = Settings(
        ollama_base_url=_str("OLLAMA_BASE_URL", "http://localhost:11434"),
        writer_model=_str("WRITER_MODEL", "qwen3:8b"),
        fast_model=_str("FAST_MODEL", "qwen3:4b"),
        script_model=_str("SCRIPT_MODEL") or _str("WRITER_MODEL", "qwen3:8b"),
        writer_ctx=_int("WRITER_NUM_CTX", 16384),
        fast_ctx=_int("FAST_NUM_CTX", 8192),
        llm_timeout=_int("LLM_TIMEOUT_SECONDS", 1800),
        llm_concurrency=_int("LLM_CONCURRENCY", 1),
        mail_backend=_str("MAIL_BACKEND", "file").lower(),
        smtp_host=_str("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_int("SMTP_PORT", 587),
        imap_host=_str("IMAP_HOST", "imap.gmail.com"),
        smtp_user=_str("SMTP_USER"),
        smtp_app_password=_str("SMTP_APP_PASSWORD").replace(" ", ""),
        approver_email=_str("APPROVER_EMAIL").lower(),
        sender_name=_str("SENDER_NAME", "Content Agent"),
        pexels_api_key=_str("PEXELS_API_KEY"),
        unsplash_access_key=_str("UNSPLASH_ACCESS_KEY"),
        google_api_key=_str("GOOGLE_API_KEY"),
        gemini_image_model=_str("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
        max_retries=_int("MAX_RETRIES", 3),
        blog_min_words=_int("BLOG_MIN_WORDS", 2500),
        blog_min_sections=_int("BLOG_MIN_SECTIONS", 5),
        blog_recency_days=_int("BLOG_RECENCY_DAYS", 7),
        yt_recency_days=_int("YT_RECENCY_DAYS", 2),
        yt_target_words=_int("YT_TARGET_WORDS", 1300),
        script_language=_str("SCRIPT_LANGUAGE", "hindi").lower(),
        blog_schedule=_str("BLOG_SCHEDULE", "mon 08:00").lower(),
        yt_schedule=_str("YT_SCHEDULE", "daily 07:00").lower(),
        poll_minutes=_int("POLL_MINUTES", 10),
        data_dir=data_dir,
    )
    s.ensure_dirs()
    return s
