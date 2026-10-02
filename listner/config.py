from __future__ import annotations
from dataclasses import dataclass
import os


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    # Production uses the shared PostgreSQL URL. DATABASE_PATH is intentionally
    # opt-in for local SQLite development only.
    database_url: str | None = os.getenv("DATABASE_URL")
    database_path: str | None = os.getenv("DATABASE_PATH")
    poll_seconds: float = float(os.getenv("POLL_SECONDS", "2"))
    lease_seconds: int = int(os.getenv("LEASE_SECONDS", "10"))
    telegram_api_id: int | None = int(os.environ["TELEGRAM_API_ID"]) if os.getenv("TELEGRAM_API_ID") else None
    telegram_api_hash: str | None = os.getenv("TELEGRAM_API_HASH")
    telegram_session: str = os.getenv("TELEGRAM_SESSION", "/data/telethon.session")
    bot_token: str | None = os.getenv("BOT_TOKEN")
    bot_chat_id: str | None = os.getenv("BOT_CHAT_ID")
    control_secret: str | None = os.getenv("CONTROL_SECRET")

    @property
    def database(self) -> str:
        if self.database_url:
            return self.database_url
        if self.database_path:
            return self.database_path
        raise RuntimeError("DATABASE_URL is required (DATABASE_PATH is local-development fallback only)")
