from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from collections.abc import Callable
from datetime import datetime

from .contacts import ContactStore, ScheduledMessage
from .personal_productivity import PersonalProductivityStore


TelegramSender = Callable[[int, str], str | None]
SentHook = Callable[[ScheduledMessage, str | None], None]
logger = logging.getLogger(__name__)


def dispatch_due_messages(
    store: ContactStore,
    sender: TelegramSender,
    *,
    now: str | datetime | None = None,
    limit: int = 20,
    on_sent: SentHook | None = None,
) -> dict[str, int]:
    current = datetime.fromisoformat(now.replace("Z", "+00:00")) if isinstance(now, str) else now
    messages = store.claim_due_messages(now=current, limit=limit)
    counts = {"claimed": len(messages), "sent": 0, "failed": 0}
    for message in messages:
        try:
            external_id = sender(message.telegram_chat_id, message.text)
        except Exception as error:  # One failed recipient must not block the rest.
            store.mark_message_failed(message.id, error=str(error) or error.__class__.__name__)
            counts["failed"] += 1
            continue
        store.mark_message_sent(message.id, external_id=external_id)
        if on_sent is not None:
            try:
                on_sent(message, external_id)
            except Exception:
                logger.exception("Could not append sent Telegram message %s to CRM", message.id)
        counts["sent"] += 1
    return counts


def dispatch_due_reminders(
    store: PersonalProductivityStore,
    sender: TelegramSender,
    *,
    chat_id: int,
    now: str | datetime | None = None,
    limit: int = 20,
) -> dict[str, int]:
    reminders = store.claim_due_reminders(now=now, limit=limit)
    counts = {"claimed": len(reminders), "sent": 0, "failed": 0}
    for reminder in reminders:
        try:
            sender(int(chat_id), reminder.text)
        except Exception as error:
            store.release_failed_reminder(reminder.id, error=str(error) or error.__class__.__name__)
            counts["failed"] += 1
            continue
        store.mark_reminder_delivered(reminder.id, now=now)
        counts["sent"] += 1
    return counts


class HermesTelegramSender:
    def __init__(
        self,
        command: str | list[str] | tuple[str, ...] | None = None,
        *,
        timeout_seconds: float = 20,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        configured = command if command is not None else os.getenv("HERMES_NATIVE_SEND_COMMAND", "hermes")
        self.command = _command_argv(configured)
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def __call__(self, chat_id: int, text: str) -> str | None:
        result = self.runner(
            [*self.command, "send", "--json", "--to", f"telegram:{chat_id}", text],
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


def _command_argv(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    command = tuple(shlex.split(value) if isinstance(value, str) else value)
    if not command or not all(part.strip() for part in command):
        raise ValueError("HERMES_NATIVE_SEND_COMMAND must contain a command.")
    return command
