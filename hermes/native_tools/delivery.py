from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime

from .contacts import ContactStore


TelegramSender = Callable[[int, str], str | None]


def dispatch_due_messages(
    store: ContactStore,
    sender: TelegramSender,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> dict[str, int]:
    messages = store.claim_due_messages(now=now, limit=limit)
    counts = {"claimed": len(messages), "sent": 0, "failed": 0}
    for message in messages:
        try:
            external_id = sender(message.telegram_chat_id, message.text)
        except Exception as error:  # One failed recipient must not block the rest.
            store.mark_message_failed(message.id, error=str(error) or error.__class__.__name__)
            counts["failed"] += 1
            continue
        store.mark_message_sent(message.id, external_id=external_id)
        counts["sent"] += 1
    return counts


class HermesTelegramSender:
    def __init__(self, command: str = "hermes", *, timeout_seconds: float = 20) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def __call__(self, chat_id: int, text: str) -> str | None:
        result = subprocess.run(
            [self.command, "send", "--json", "--to", f"telegram:{chat_id}", text],
            capture_output=True,
            check=False,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Telegram delivery failed").strip()
            raise RuntimeError(detail[:500])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        for key in ("message_id", "id", "external_id"):
            if payload.get(key) is not None:
                return f"telegram:{payload[key]}"
        return None
