from __future__ import annotations

import re

from assistant.types import Intent, ParsedMessage


COMMAND_INTENTS = {
    "/ask": Intent.ASK,
    "/remember": Intent.REMEMBER,
    "/memories": Intent.MEMORIES,
    "/idea": Intent.IDEA,
    "/ideas": Intent.IDEAS,
    "/remind": Intent.REMIND,
    "/reminders": Intent.REMINDERS,
    "/cancel_reminder": Intent.CANCEL_REMINDER,
    "/task": Intent.TASK,
    "/tasks": Intent.TASKS,
    "/task_done": Intent.TASK_DONE,
    "/task_move": Intent.TASK_MOVE,
    "/task_batch": Intent.TASK_BATCH,
    "/calendar": Intent.CALENDAR,
    "/do": Intent.AGENT_DO,
    "/jobs": Intent.AGENT_JOBS,
    "/job": Intent.AGENT_JOB,
    "/monitor": Intent.MONITOR_LIST,
    "/trace": Intent.TRACE,
    "/status": Intent.STATUS,
    "/admin_status": Intent.ADMIN_STATUS,
    "/help": Intent.HELP,
    "/start": Intent.HELP,
}


NATURAL_PATTERNS: list[tuple[re.Pattern[str], Intent]] = [
    (re.compile(r"^(?:идея|запиши\s+идею|сохрани\s+идею|запиши\s+мысль|сохрани\s+мысль)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.IDEA),
    (re.compile(r"^(?:запомни|сохрани\s+важное|важно)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.REMEMBER),
    (re.compile(r"^(?:напомни|поставь\s+напоминание)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.REMIND),
    (re.compile(r"^(?:создай\s+задачу|добавь\s+задачу|заведи\s+задачу)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.TASK),
    (re.compile(r"^(?:создай\s+задачи|добавь\s+задачи|заведи\s+задачи|раскидай\s+задачи|запланируй\s+задачи)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.TASK_BATCH),
    (re.compile(r"^(?:покажи\s+задачи|список\s+задач)(?:[:\s]+(?P<text>.*))?$", re.IGNORECASE | re.DOTALL), Intent.TASKS),
    (re.compile(r"^(?:поставь\s+в\s+календарь|добавь\s+в\s+календарь|создай\s+событие)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.CALENDAR),
    (re.compile(r"^(?:гермес|агент|брошка)\s+(?:сделай|выполни|разбери|запусти)[:\s]+(?P<text>.+)$", re.IGNORECASE | re.DOTALL), Intent.AGENT_DO),
    (re.compile(r"^(?:покажи\s+джобы|покажи\s+очередь|статус\s+джоб)(?:[:\s]+(?P<text>.*))?$", re.IGNORECASE | re.DOTALL), Intent.AGENT_JOBS),
]


def parse_message(text: str, *, plain_text_ai_enabled: bool = False) -> ParsedMessage:
    raw_text = text or ""
    stripped = raw_text.strip()
    if not stripped:
        return ParsedMessage(Intent.UNKNOWN, "", raw_text)

    first, _, rest = stripped.partition(" ")
    command = first.lower()
    if command == "/monitor":
        return _parse_monitor_command(rest.strip(), raw_text)
    if command in COMMAND_INTENTS:
        return ParsedMessage(COMMAND_INTENTS[command], rest.strip(), raw_text)

    for pattern, intent in NATURAL_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return ParsedMessage(intent, match.group("text").strip(), raw_text)

    if _looks_like_task_batch(stripped):
        return ParsedMessage(Intent.TASK_BATCH, stripped, raw_text)

    if plain_text_ai_enabled:
        return ParsedMessage(Intent.ASK, stripped, raw_text)

    return ParsedMessage(Intent.UNKNOWN, stripped, raw_text)


def _looks_like_task_batch(text: str) -> bool:
    lowered = text.lower()
    task_markers = len(re.findall(r"(?:^|[\n,;])\s*(?:задача\s*)?\d+[\).:\-]?\s+", lowered))
    named_markers = len(re.findall(r"\bзадача\s+\d+", lowered))
    time_markers = len(re.findall(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b", lowered))
    return (task_markers >= 2 or named_markers >= 2) and time_markers >= 1


def _parse_monitor_command(text: str, raw_text: str) -> ParsedMessage:
    action, _, rest = (text or "").strip().partition(" ")
    normalized = action.lower()
    if normalized == "add":
        return ParsedMessage(Intent.MONITOR_ADD, rest.strip(), raw_text)
    if normalized == "remove":
        return ParsedMessage(Intent.MONITOR_REMOVE, rest.strip(), raw_text)
    if normalized in {"", "list"}:
        return ParsedMessage(Intent.MONITOR_LIST, rest.strip(), raw_text)
    return ParsedMessage(Intent.UNKNOWN, (text or "").strip(), raw_text)
