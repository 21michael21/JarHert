from __future__ import annotations

from assistant.action_schema import ActionType, PlannedAction
from assistant.preferences import UserPreferences


def help_text() -> str:
    return "\n".join(
        [
            "Я умею:",
            "/ask вопрос — спросить AI",
            "/idea текст — записать идею",
            "/ideas — показать идеи",
            "/remember текст — сохранить важное",
            "/remind через 2 часа текст — поставить напоминание",
            "/reminders — список напоминаний",
            "/task название | list=Today | project=Personal | priority=P2 — создать Trello-задачу",
            "Можно просто: задача 1 проверить сервер в 10:00, задача 2 созвон в 12:00",
            "/tasks Today — показать задачи",
            "/calendar название | start=2026-07-10 10:00 | end=2026-07-10 10:30 — создать событие",
            "/do цель — поставить агентскую задачу в очередь",
            "/jobs — показать очередь агента",
            "/job id — показать детали агентской задачи",
            "/trace trace_id — показать путь job/action/delivery/event, только для админа",
            "Можно отправить голосовое: я расшифрую и выполню команду.",
        ]
    )


def should_try_llm_action_extractor(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "надо",
        "нужно",
        "организуй",
        "сделай",
        "разложи",
        "запланируй",
        "подготовь",
        "добавь",
        "создай",
        "перенеси",
        "перемести",
        "сохрани",
        "запиши",
        "напомни",
        "поставь",
        "задач",
        "календар",
    )
    return any(marker in lowered for marker in markers)


def task_text_with_preferences(text: str, preferences: UserPreferences | None) -> str:
    if preferences is None:
        return text
    value = (text or "").strip()
    lowered = value.lower()
    if preferences.default_trello_list and "list=" not in lowered and "список=" not in lowered:
        value += f" | list={preferences.default_trello_list}"
    if preferences.default_project and "project=" not in lowered and "проект=" not in lowered:
        value += f" | project={preferences.default_project}"
    return value


def natural_action_label(action: PlannedAction) -> str:
    return action.payload.get("title") or action.payload.get("text") or action.payload.get("goal") or action.type.value


def task_payload(text: str) -> dict[str, str]:
    fields = fields_payload(text, fallback_key="title")
    payload = {"title": fields.get("title", "")}
    for key in ("start", "end", "list", "project"):
        if fields.get(key):
            payload[key] = fields[key]
    return payload


def fields_payload(text: str, *, fallback_key: str) -> dict[str, str]:
    chunks = [chunk.strip() for chunk in (text or "").split("|") if chunk.strip()]
    fields: dict[str, str] = {}
    if chunks and "=" not in chunks[0]:
        fields[fallback_key] = chunks.pop(0)
    for chunk in chunks:
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        normalized = normalize_field_key(key)
        clean_value = value.strip()
        if normalized and clean_value:
            fields[normalized] = clean_value
    return fields


def normalize_field_key(key: str) -> str:
    normalized = key.strip().lower()
    return {
        "название": "title",
        "текст": "text",
        "список": "list",
        "проект": "project",
        "куда": "to",
        "начало": "start",
        "конец": "end",
    }.get(normalized, normalized)


def is_heavy_action(action: PlannedAction) -> bool:
    return action.type in {
        ActionType.TASK_CREATE,
        ActionType.TASK_LIST,
        ActionType.TASK_MOVE,
        ActionType.TASK_DONE,
        ActionType.CALENDAR_CREATE,
        ActionType.CALENDAR_MOVE,
    }
