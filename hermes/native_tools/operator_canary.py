from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol


class OperatorCanaryError(RuntimeError):
    pass


class TaskCalendarCanaryAdapter(Protocol):
    def create_task(self, *, title: str, **kwargs: object) -> str: ...
    def delete_task(self, *, title: str) -> str: ...
    def create_calendar_event(self, *, title: str, **kwargs: object) -> str: ...
    def delete_calendar_event(self, *, title: str) -> str: ...


class ProductivityCanaryApi(Protocol):
    def reminder_create(self, **payload: object) -> dict[str, Any]: ...
    def reminder_cancel(self, *, reminder_id: int) -> dict[str, Any]: ...


TelegramSender = Callable[[int, str], str | None]


def run_operator_canary(
    *,
    api: ProductivityCanaryApi,
    adapter: TaskCalendarCanaryAdapter,
    sender: TelegramSender,
    chat_id: int,
    run_id: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Exercise real integrations with uniquely named resources and guaranteed cleanup."""
    clean_run_id = _run_id(run_id)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    title = f"[JarHert canary {clean_run_id}]"
    start = current + timedelta(days=14)
    end = start + timedelta(minutes=15)
    reminder_id: int | None = None
    task_created = False
    calendar_created = False
    primary_error: OperatorCanaryError | None = None
    try:
        adapter.create_task(title=title, list_name="Inbox", description="Temporary operator canary; cleaned up immediately.")
        task_created = True
        adapter.create_calendar_event(
            title=title,
            start=start.strftime("%Y-%m-%d %H:%M"),
            end=end.strftime("%Y-%m-%d %H:%M"),
            description="Temporary operator canary; cleaned up immediately.",
        )
        calendar_created = True
        reminder = api.reminder_create(
            text=f"Temporary JarHert canary {clean_run_id}",
            remind_at=(current + timedelta(days=30)).isoformat(),
            idempotency_key=f"operator-canary:{clean_run_id}:reminder",
        )
        reminder_id = int(reminder["id"])
        try:
            sender(int(chat_id), f"JarHert operator canary {clean_run_id}: delivery is working.")
        except Exception as error:
            raise OperatorCanaryError("Telegram delivery failed.") from error
    except OperatorCanaryError as error:
        primary_error = error
    except Exception as error:
        primary_error = OperatorCanaryError(f"Integration canary failed: {type(error).__name__}.")
    cleanup_errors = _cleanup(
        api=api,
        adapter=adapter,
        reminder_id=reminder_id,
        title=title,
        calendar_created=calendar_created,
        task_created=task_created,
    )
    if primary_error is not None:
        raise primary_error
    if cleanup_errors:
        raise OperatorCanaryError("Integration canary cleanup failed: " + ", ".join(cleanup_errors))
    return {
        "ok": True,
        "run_id": clean_run_id,
        "telegram_sent": True,
        "task_cleaned": task_created,
        "calendar_cleaned": calendar_created,
        "reminder_cleaned": reminder_id is not None,
    }


def _cleanup(
    *,
    api: ProductivityCanaryApi,
    adapter: TaskCalendarCanaryAdapter,
    reminder_id: int | None,
    title: str,
    calendar_created: bool,
    task_created: bool,
) -> list[str]:
    errors: list[str] = []
    actions: list[tuple[str, Callable[[], object]]] = []
    if reminder_id is not None:
        actions.append(("reminder", lambda: api.reminder_cancel(reminder_id=reminder_id)))
    if calendar_created:
        actions.append(("calendar", lambda: adapter.delete_calendar_event(title=title)))
    if task_created:
        actions.append(("task", lambda: adapter.delete_task(title=title)))
    for name, action in actions:
        try:
            action()
        except Exception as error:
            errors.append(f"{name}:{type(error).__name__}")
    return errors


def _run_id(value: str) -> str:
    clean = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", clean):
        raise ValueError("Canary run id must be 6-64 letters, numbers, underscores, or hyphens.")
    return clean
