from assistant.intents import parse_message
from assistant.types import Intent


def test_parse_ask_command() -> None:
    parsed = parse_message("/ask объясни MVP")
    assert parsed.intent == Intent.ASK
    assert parsed.text == "объясни MVP"


def test_parse_idea_command() -> None:
    parsed = parse_message("/idea сделать быстрый черновик")
    assert parsed.intent == Intent.IDEA
    assert parsed.text == "сделать быстрый черновик"


def test_parse_natural_idea() -> None:
    parsed = parse_message("запиши идею сделать голосовые заметки")
    assert parsed.intent == Intent.IDEA
    assert parsed.text == "сделать голосовые заметки"


def test_parse_natural_remember() -> None:
    parsed = parse_message("запомни проверить Google Docs")
    assert parsed.intent == Intent.REMEMBER
    assert parsed.text == "проверить Google Docs"


def test_parse_natural_reminder() -> None:
    parsed = parse_message("напомни через 2 часа проверить бота")
    assert parsed.intent == Intent.REMIND
    assert parsed.text == "через 2 часа проверить бота"


def test_parse_task_command() -> None:
    parsed = parse_message("/task сделать интеграцию")
    assert parsed.intent == Intent.TASK
    assert parsed.text == "сделать интеграцию"


def test_parse_natural_task() -> None:
    parsed = parse_message("создай задачу проверить Trello")
    assert parsed.intent == Intent.TASK
    assert parsed.text == "проверить Trello"


def test_parse_natural_calendar_event() -> None:
    parsed = parse_message("поставь в календарь созвон | start=2026-07-10 10:00 | end=2026-07-10 10:30")
    assert parsed.intent == Intent.CALENDAR
    assert parsed.text == "созвон | start=2026-07-10 10:00 | end=2026-07-10 10:30"


def test_parse_plain_task_batch_before_ai() -> None:
    parsed = parse_message(
        "задача 1 проверить сервер в 10:00, задача 2 созвон в 12:00",
        plain_text_ai_enabled=True,
    )
    assert parsed.intent == Intent.TASK_BATCH


def test_parse_agent_commands() -> None:
    assert parse_message("/do разложи задачи").intent == Intent.AGENT_DO
    assert parse_message("/jobs").intent == Intent.AGENT_JOBS
    assert parse_message("/job 1").intent == Intent.AGENT_JOB


def test_parse_monitor_commands() -> None:
    add = parse_message(
        "/monitor add github_releases openai/codex | condition=напиши если вышел важный релиз"
    )
    assert add.intent == Intent.MONITOR_ADD
    assert add.text == "github_releases openai/codex | condition=напиши если вышел важный релиз"
    assert parse_message("/monitor list").intent == Intent.MONITOR_LIST
    assert parse_message("/monitor remove 12").intent == Intent.MONITOR_REMOVE


def test_parse_natural_agent_request() -> None:
    parsed = parse_message("Гермес сделай: проверь задачи и календарь")

    assert parsed.intent == Intent.AGENT_DO
    assert parsed.text == "проверь задачи и календарь"


def test_plain_text_disabled_by_default() -> None:
    parsed = parse_message("привет")
    assert parsed.intent == Intent.UNKNOWN


def test_plain_text_can_be_ai() -> None:
    parsed = parse_message("привет", plain_text_ai_enabled=True)
    assert parsed.intent == Intent.ASK
