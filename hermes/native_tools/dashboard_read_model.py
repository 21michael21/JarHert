"""Read-only DTO assembly for the authenticated JarHert dashboard."""

from __future__ import annotations

from typing import Any, Callable


def build_dashboard_snapshot(api: Any) -> dict[str, Any]:
    """Collect a compact cabinet view without letting one integration hide the rest."""
    today = safe_read(api.personal_today, fallback={})
    status = safe_read(api.system_status, fallback={})
    reminders = safe_read(lambda: api.reminder_list(status="active", limit=10), fallback={"items": []})
    notes = safe_read(lambda: api.memory_block_list(block_type="note", limit=6), fallback={"items": []})
    monitors = safe_read(api.monitor_list, fallback={"items": []})
    projects = safe_read(api.project_context_list, fallback={"items": []})
    integrations = safe_read(api.integration_health, fallback={})
    work_mode = safe_read(api.work_mode_get, fallback={"mode": "fast"})
    tasks = external_items(today.get("tasks"))
    priorities = list(today.get("top_three") or [])[:3]
    if not priorities:
        priorities = [{"title": task, "type": "task"} for task in tasks[:3]]
    return {
        "today": {
            "tasks": tasks,
            "calendar": external_items(today.get("calendar")),
            "reminders": items(reminders),
            "priorities": priorities,
        },
        "notes": items(notes),
        "status": status,
        "integrations": integrations,
        "work_mode": work_mode,
        "monitors": items(monitors),
        "projects": items(projects),
        "capabilities": dashboard_capabilities(),
    }


def safe_read(operation: Callable[[], Any], *, fallback: Any) -> Any:
    try:
        return operation()
    except Exception:
        return fallback


def items(payload: Any) -> list[dict[str, Any]]:
    return [item for item in (payload or {}).get("items", []) if isinstance(item, dict)][:10]


def external_items(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw or raw.casefold().rstrip(".!") in {"no events found", "событий нет"}:
        return []
    rows = [row.strip(" -•\t") for row in raw.splitlines() if row.strip()]
    return [row.split("|", 1)[0].replace("[open]", "").strip()[:160] for row in rows[:10]]


def dashboard_capabilities() -> list[dict[str, str]]:
    return [
        {"title": "План дня", "text": "Календарь, Trello, напоминания и три главных приоритета."},
        {"title": "Память", "text": "Заметки, проекты, люди, обещания и поиск по ним."},
        {"title": "Автоматизация", "text": "Напоминания, отложенные сообщения, сводки и monitors."},
        {"title": "Интеграции", "text": "Trello и Google Calendar через один подтверждённый план."},
        {"title": "Режимы", "text": "Быстро, думаю и код: с разными правами и лимитами."},
        {"title": "Безопасность", "text": "Код только в sandbox; важные действия подтверждаются в Telegram."},
    ]
