from __future__ import annotations

import re

from assistant.action_schema import ActionType, NaturalRoute, PlannedAction
from assistant.natural_tasks import parse_natural_task_batch
from assistant.preferences import UserPreferences


TIME_RE = re.compile(r"\b(?:в|на|к)\s+((?:[01]?\d|2[0-3])(?::[0-5]\d)?)\b", re.IGNORECASE)
PERIOD_CLOCKS = {
    "утром": "09:00",
    "вечером": "19:00",
}
WEEKDAYS = {
    "понедельник": "monday",
    "вторник": "tuesday",
    "среду": "wednesday",
    "четверг": "thursday",
    "пятницу": "friday",
    "субботу": "saturday",
    "воскресенье": "sunday",
}
CALENDAR_WORDS = ("созвон", "встреч", "демо", "колл", "звонок")
TASK_WORDS = ("задач", "проверь", "проверить", "сделай", "подготовь", "разбер", "обнови")


def route_natural_text(
    text: str,
    *,
    context_text: str | None = None,
    preferences: UserPreferences | None = None,
) -> NaturalRoute:
    value = " ".join((text or "").strip().split())
    if not value or value.startswith("/"):
        return NaturalRoute()

    actions = _route_mixed(value, context_text=context_text, preferences=preferences)
    if not actions:
        actions = _route_single(value, context_text=context_text, preferences=preferences)
    if not actions:
        return NaturalRoute(actions=[], fallback_to_ai=True, reason="no_action")
    return NaturalRoute(actions=_apply_preferences(actions, preferences), fallback_to_ai=False, reason="deterministic")


def _route_mixed(
    text: str,
    *,
    context_text: str | None,
    preferences: UserPreferences | None,
) -> list[PlannedAction]:
    lowered = text.lower()
    if " и " not in lowered:
        return []
    actions: list[PlannedAction] = []
    for part in re.split(r"\s+и\s+", text, flags=re.IGNORECASE):
        part_actions = _route_single(part.strip(), context_text=context_text, preferences=preferences)
        if not part_actions:
            return []
        actions.extend(part_actions)
    return actions


def _route_single(
    text: str,
    *,
    context_text: str | None,
    preferences: UserPreferences | None,
) -> list[PlannedAction]:
    if _is_secret_request(text):
        return []

    matchers = (
        lambda value: _idea_action(value, context_text=context_text),
        lambda value: _memory_action(value, context_text=context_text),
        _reminder_action,
        _task_list_action,
        _task_done_action,
        _task_move_action,
        lambda value: _ambiguous_move_action(value, preferences=preferences),
        lambda value: _calendar_move_action(value, preferences=preferences),
        _agent_job_action,
        lambda value: _task_actions(value, preferences=preferences),
        lambda value: _calendar_action(value, preferences=preferences),
    )
    for matcher in matchers:
        actions = matcher(text)
        if actions:
            return actions
    return []


def _idea_action(text: str, *, context_text: str | None = None) -> list[PlannedAction]:
    context_match = re.match(r"^(?:запиши|сохрани)\s+это\s+как\s+идею$", text, re.IGNORECASE)
    if context_match:
        if context_text:
            return [_action(ActionType.IDEA_SAVE, text=context_text)]
        return [PlannedAction(ActionType.IDEA_SAVE, payload={"text": "это"}, confidence=0.65, needs_confirmation=True)]
    match = re.match(r"^(?:запиши\s+идею|сохрани\s+идею|идея)[:\s]+(?P<text>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    return [_action(ActionType.IDEA_SAVE, text=match.group("text").strip())]


def _memory_action(text: str, *, context_text: str | None = None) -> list[PlannedAction]:
    context_match = re.match(r"^(?:запиши|сохрани)\s+это\s+как\s+важное$", text, re.IGNORECASE)
    if context_match:
        if context_text:
            return [_action(ActionType.MEMORY_SAVE, text=context_text)]
        return [PlannedAction(ActionType.MEMORY_SAVE, payload={"text": "это"}, confidence=0.65, needs_confirmation=True)]
    match = re.match(r"^(?:запомни|сохрани\s+важное|важно|сохрани\s+мысль|запиши\s+мысль)[:\s]+(?P<text>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    return [_action(ActionType.MEMORY_SAVE, text=_strip_optional_colon(match.group("text").strip()))]


def _reminder_action(text: str) -> list[PlannedAction]:
    match = re.match(r"^(?:напомни|поставь\s+напоминание)[:\s]+(?P<text>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    return [_action(ActionType.REMINDER_CREATE, text=match.group("text").strip())]


def _task_list_action(text: str) -> list[PlannedAction]:
    lowered = text.lower()
    if re.match(r"^(?:что\s+у\s+меня|какие\s+задачи).*\bсегодня\b", lowered):
        return [PlannedAction(ActionType.TASK_LIST, payload={"list": "Today"}, confidence=0.9)]
    if re.match(r"^(?:что\s+у\s+меня|покажи\s+план).*\bзавтра\b", lowered):
        return [PlannedAction(ActionType.TASK_LIST, payload={"list": "Next"}, confidence=0.82)]
    if re.match(r"^покажи\s+план.*\bнедел", lowered):
        return [PlannedAction(ActionType.TASK_LIST, payload={"list": "Backlog"}, confidence=0.78)]
    match = re.match(r"^(?:покажи\s+задачи|список\s+задач)(?:\s+(?P<list>.+))?$", text, re.IGNORECASE)
    if not match:
        return []
    list_name = (match.group("list") or "").strip()
    payload = {"list": list_name} if list_name else {}
    return [PlannedAction(ActionType.TASK_LIST, payload=payload, confidence=0.95)]


def _task_done_action(text: str) -> list[PlannedAction]:
    match = re.match(r"^(?:закрой|закрыть|выполни|заверши)\s+задачу\s+(?P<title>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    return [_action(ActionType.TASK_DONE, title=match.group("title").strip())]


def _task_move_action(text: str) -> list[PlannedAction]:
    match = re.match(r"^(?:перенеси|перемести)\s+задачу\s+(?P<title>.+?)\s+в\s+(?P<to>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\s-]*)$", text, re.IGNORECASE)
    if not match:
        return []
    return [_action(ActionType.TASK_MOVE, title=match.group("title").strip(), to=match.group("to").strip())]


def _ambiguous_move_action(text: str, *, preferences: UserPreferences | None = None) -> list[PlannedAction]:
    match = re.match(r"^перенеси\s+(?:её|ее|его|это)\s+на\s+(?P<when>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    _, start, end = _timed_title(match.group("when"), preferences=preferences)
    payload = {"title": "это"}
    if start:
        payload["start"] = start
    if end:
        payload["end"] = end
    return [PlannedAction(ActionType.CALENDAR_MOVE, payload=payload, confidence=0.45, needs_confirmation=True)]


def _calendar_move_action(text: str, *, preferences: UserPreferences | None = None) -> list[PlannedAction]:
    match = re.match(r"^перенеси\s+(?P<title>встречу|созвон|колл|звонок)(?P<rest>.*?)\s+на\s+(?P<when>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    title = f"{match.group('title')}{match.group('rest')}".strip()
    _, start, end = _timed_title(match.group("when"), preferences=preferences)
    payload = {"title": title}
    if start:
        payload["start"] = start
    if end:
        payload["end"] = end
    return [PlannedAction(ActionType.CALENDAR_MOVE, payload=payload, confidence=0.72, needs_confirmation=True)]


def _agent_job_action(text: str) -> list[PlannedAction]:
    match = re.match(r"^(?:гермес|агент|брошка)\s+(?:сделай|выполни|разбери|запусти)\s+(?P<goal>.+)$", text, re.IGNORECASE)
    if not match:
        return []
    return [_action(ActionType.AGENT_JOB_CREATE, goal=match.group("goal").strip())]


def _calendar_action(text: str, *, preferences: UserPreferences | None = None) -> list[PlannedAction]:
    explicit = re.match(r"^(?:поставь\s+в\s+календарь|добавь\s+в\s+календарь|создай\s+событие)\s+(?P<body>.+)$", text, re.IGNORECASE)
    if explicit:
        title, start, end = _timed_title(explicit.group("body"), preferences=preferences)
        if start and end:
            return [_action(ActionType.CALENDAR_CREATE, title=title, start=start, end=end)]
    if _looks_like_calendar(text, preferences=preferences):
        title, start, end = _timed_title(text, preferences=preferences)
        if start and end:
            return [_action(ActionType.CALENDAR_CREATE, title=title, start=start, end=end)]
    return []


def _task_actions(text: str, *, preferences: UserPreferences | None = None) -> list[PlannedAction]:
    natural_tasks = parse_natural_task_batch(text)
    if len(natural_tasks) >= 2:
        return [
            _timed_task_action(task.title, task.start, task.end)
            for task in natural_tasks
        ]

    explicit = re.match(r"^(?:создай|добавь|заведи)\s+задачу\s+(?P<title>.+)$", text, re.IGNORECASE)
    if explicit:
        return [_action(ActionType.TASK_CREATE, title=explicit.group("title").strip())]

    due = _due_task_action(text)
    if due:
        return [due]

    if _looks_like_task(text, preferences=preferences):
        title, start, end = _timed_title(text, preferences=preferences)
        if start and end:
            return [_timed_task_action(title, start, end)]
    return []


def _due_task_action(text: str) -> PlannedAction | None:
    lowered = text.lower()
    if lowered.startswith("на этой неделе "):
        title = _clean_title(text[len("на этой неделе ") :])
        return _action(ActionType.TASK_CREATE, title=title, due="this_week")
    if lowered.startswith("до завтра "):
        title = _clean_title(text[len("до завтра ") :])
        return _action(ActionType.TASK_CREATE, title=title, due="tomorrow")
    return None


def _timed_task_action(title: str, start: str | None, end: str | None) -> PlannedAction:
    payload = {"title": title}
    if start and end:
        payload["start"] = start
        payload["end"] = end
    return PlannedAction(ActionType.TASK_CREATE, payload=payload, confidence=0.9)


def _timed_title(text: str, *, preferences: UserPreferences | None = None) -> tuple[str, str | None, str | None]:
    match = TIME_RE.search(text)
    period_clock = _period_clock(text, preferences=preferences)
    if not match and not period_clock:
        return (_clean_title(text), None, None)
    clock = _normalize_clock(match.group(1)) if match else period_clock or "09:00"
    date_prefix = _date_prefix(text)
    raw_title = (text[: match.start()] + text[match.end() :]).strip() if match else text
    title = _clean_title(raw_title)
    return title, f"{date_prefix} {clock}", f"{date_prefix} {_add_minutes(clock, 30)}"


def _date_prefix(text: str) -> str:
    lowered = text.lower()
    for russian, english in WEEKDAYS.items():
        if russian in lowered:
            return english
    if "послезавтра" in lowered:
        return "day_after_tomorrow"
    if "завтра" in lowered:
        return "tomorrow"
    if "сегодня" in lowered:
        return "today"
    return "today"


def _period_clock(text: str, *, preferences: UserPreferences | None = None) -> str | None:
    lowered = text.lower()
    if "утром" in lowered:
        return preferences.morning_time if preferences else PERIOD_CLOCKS["утром"]
    if "вечером" in lowered:
        return preferences.evening_time if preferences else PERIOD_CLOCKS["вечером"]
    return None


def _normalize_clock(value: str) -> str:
    if ":" in value:
        hours, minutes = value.split(":", 1)
        return f"{int(hours):02d}:{minutes}"
    return f"{int(value):02d}:00"


def _add_minutes(clock: str, minutes: int) -> str:
    hours, raw_minutes = [int(part) for part in clock.split(":", 1)]
    total = (hours * 60 + raw_minutes + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _clean_title(value: str) -> str:
    title = re.sub(r"\b(?:сегодня|завтра|послезавтра)\b", "", value, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:утром|вечером)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:поставь|добавь|создай|событие|задачу)\\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:в|на|к)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:в|на|к)\s*$", "", title, flags=re.IGNORECASE)
    return " ".join(title.strip(" .,-—:").split())


def _looks_like_calendar(text: str, *, preferences: UserPreferences | None = None) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in CALENDAR_WORDS) and (
        TIME_RE.search(text) is not None or _period_clock(text, preferences=preferences) is not None
    )


def _looks_like_task(text: str, *, preferences: UserPreferences | None = None) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in TASK_WORDS) and (
        TIME_RE.search(text) is not None or _period_clock(text, preferences=preferences) is not None
    )


def _is_secret_request(text: str) -> bool:
    lowered = text.lower()
    has_secret_marker = any(marker in lowered for marker in (".env", "секрет", "токен", "token", "secret"))
    has_access_verb = any(verb in lowered for verb in ("прочитай", "покажи", "выведи", "скинь", "открой", "read", "show", "print"))
    return has_secret_marker and has_access_verb


def _action(action_type: ActionType, **payload: str) -> PlannedAction:
    return PlannedAction(action_type, payload={key: value for key, value in payload.items() if value}, confidence=0.95)


def _apply_preferences(actions: list[PlannedAction], preferences: UserPreferences | None) -> list[PlannedAction]:
    if preferences is None:
        return actions
    updated: list[PlannedAction] = []
    for action in actions:
        if action.type != ActionType.TASK_CREATE:
            updated.append(action)
            continue
        payload = dict(action.payload)
        payload.setdefault("list", preferences.default_trello_list)
        if preferences.default_project:
            payload.setdefault("project", preferences.default_project)
        updated.append(
            PlannedAction(
                action.type,
                payload=payload,
                confidence=action.confidence,
                needs_confirmation=action.needs_confirmation,
                reason=action.reason,
            )
        )
    return updated


def _strip_optional_colon(text: str) -> str:
    return text[1:].strip() if text.startswith(":") else text
