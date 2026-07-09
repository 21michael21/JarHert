from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend.db import make_session_factory
from backend.migrations import require_current_schema
from backend.message_store import SqlCollectedMessageStore
from telegram_collector.config import CollectorSettings
from telegram_collector.health import CollectorHealth, start_health_server


logger = logging.getLogger(__name__)


async def run_collector(settings: CollectorSettings) -> None:
    settings.validate()
    try:
        from telethon import TelegramClient, events
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime dependency.
        raise RuntimeError("Install collector dependency: pip install -e . or pip install telethon") from exc

    session_factory = make_session_factory(settings.database_url)
    require_current_schema(settings.database_url)
    store = SqlCollectedMessageStore(session_factory)
    health = CollectorHealth(tracked_chats=len(settings.chats))
    start_health_server(settings.health_host, settings.health_port, health)

    chat_refs = [_telethon_chat_ref(item) for item in settings.chats]
    client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)

    @client.on(events.NewMessage(chats=chat_refs))
    async def on_new_message(event) -> None:
        try:
            message = event.message
            chat = await event.get_chat()
            sender = await event.get_sender()
            saved = store.add_message(
                chat_id=int(getattr(chat, "id", 0) or 0),
                chat_title=_chat_title(chat),
                sender_id=_entity_id(sender),
                sender_name=_sender_name(sender),
                text=message.message or "",
                timestamp=message.date or datetime.now(timezone.utc),
                telegram_message_id=int(message.id) if message.id is not None else None,
            )
            health.written_count += 1
            health.last_message_at = saved.timestamp
            health.last_error = None
        except Exception as exc:
            health.last_error = str(exc) or exc.__class__.__name__
            logger.exception("telegram collector failed to store message")

    await client.start()
    health.connected = True
    logger.info(
        "telegram collector started: chats=%s health=http://%s:%s/health",
        len(chat_refs),
        settings.health_host,
        settings.health_port,
    )
    heartbeat_task = asyncio.create_task(_heartbeat(settings.heartbeat_seconds, health))
    try:
        await client.run_until_disconnected()
    finally:
        health.connected = False
        heartbeat_task.cancel()
        await client.disconnect()


async def _heartbeat(interval_seconds: int, health: CollectorHealth) -> None:
    while True:
        logger.info(
            "telegram collector heartbeat: connected=%s tracked=%s written=%s last_message_at=%s",
            health.connected,
            health.tracked_chats,
            health.written_count,
            health.last_message_at.isoformat() if health.last_message_at else None,
        )
        await asyncio.sleep(max(10, interval_seconds))


def _telethon_chat_ref(value: str):
    clean = value.strip()
    if clean.lstrip("-").isdigit():
        return int(clean)
    return clean


def _chat_title(chat) -> str | None:
    for attr in ("title", "username", "first_name"):
        value = getattr(chat, attr, None)
        if value:
            return str(value)
    return None


def _entity_id(entity) -> int | None:
    value = getattr(entity, "id", None)
    return int(value) if value is not None else None


def _sender_name(sender) -> str | None:
    if sender is None:
        return None
    parts = [
        getattr(sender, "first_name", None),
        getattr(sender, "last_name", None),
    ]
    name = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    if name:
        return name
    username = getattr(sender, "username", None)
    return str(username) if username else None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_collector(CollectorSettings.from_env()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
