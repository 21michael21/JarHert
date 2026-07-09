from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectorSettings:
    database_url: str
    api_id: int
    api_hash: str
    session_path: str
    chats: list[str]
    health_host: str = "127.0.0.1"
    health_port: int = 8091
    heartbeat_seconds: int = 60

    @classmethod
    def from_env(cls) -> "CollectorSettings":
        api_id = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        chats = load_chat_refs(
            raw=os.getenv("TELEGRAM_COLLECTOR_CHATS", ""),
            file_path=os.getenv("TELEGRAM_COLLECTOR_CHATS_FILE", ""),
        )
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/ai_brooch.sqlite3"),
            api_id=api_id,
            api_hash=api_hash,
            session_path=os.getenv("TELEGRAM_COLLECTOR_SESSION", "./data/telegram_collector.session"),
            chats=chats,
            health_host=os.getenv("TELEGRAM_COLLECTOR_HEALTH_HOST", "127.0.0.1"),
            health_port=int(os.getenv("TELEGRAM_COLLECTOR_HEALTH_PORT", "8091")),
            heartbeat_seconds=int(os.getenv("TELEGRAM_COLLECTOR_HEARTBEAT_SECONDS", "60")),
        )

    def validate(self) -> None:
        if self.api_id <= 0:
            raise ValueError("TELEGRAM_API_ID is required for Telegram collector")
        if not self.api_hash:
            raise ValueError("TELEGRAM_API_HASH is required for Telegram collector")
        if not self.chats:
            raise ValueError("TELEGRAM_COLLECTOR_CHATS or TELEGRAM_COLLECTOR_CHATS_FILE must list at least one chat")
        session_parent = Path(self.session_path).expanduser().parent
        session_parent.mkdir(parents=True, exist_ok=True)


def load_chat_refs(*, raw: str, file_path: str = "") -> list[str]:
    if file_path.strip():
        data = json.loads(Path(file_path).expanduser().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("chats", [])
        if not isinstance(data, list):
            raise ValueError("TELEGRAM_COLLECTOR_CHATS_FILE must contain a JSON list or {\"chats\": [...]}")
        return [_normalize_chat_ref(item) for item in data if _normalize_chat_ref(item)]

    value = raw.strip()
    if not value:
        return []
    if value.startswith("["):
        data = json.loads(value)
        if not isinstance(data, list):
            raise ValueError("TELEGRAM_COLLECTOR_CHATS JSON value must be a list")
        return [_normalize_chat_ref(item) for item in data if _normalize_chat_ref(item)]
    return [_normalize_chat_ref(item) for item in value.split(",") if _normalize_chat_ref(item)]


def _normalize_chat_ref(value) -> str:
    return str(value).strip()
