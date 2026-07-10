from __future__ import annotations

import pytest

from assistant.action_schema import ActionType
from assistant.natural_router import route_natural_text


def action_types(text: str) -> list[ActionType]:
    return [action.type for action in route_natural_text(text).actions]


def test_routes_idea_without_slash_command() -> None:
    result = route_natural_text("запиши идею сделать Hub ML как личный тренажер")

    assert action_types("запиши идею сделать Hub ML как личный тренажер") == [ActionType.IDEA_SAVE]
    assert result.actions[0].payload["text"] == "сделать Hub ML как личный тренажер"


def test_routes_memory_without_slash_command() -> None:
    result = route_natural_text("запомни что сервер VDS в Амстердаме")

    assert [action.type for action in result.actions] == [ActionType.MEMORY_SAVE]
    assert result.actions[0].payload["text"] == "что сервер VDS в Амстердаме"


def test_routes_relative_reminder() -> None:
    result = route_natural_text("напомни через 2 часа проверить деплой")

    assert [action.type for action in result.actions] == [ActionType.REMINDER_CREATE]
    assert result.actions[0].payload["text"] == "через 2 часа проверить деплой"


def test_routes_live_style_reminder_phrase() -> None:
    result = route_natural_text(
        "Бро просто напоминалку чтобы я завтра в часов 12 дня напоминалка пришла что пора заниматься ml"
    )

    assert [action.type for action in result.actions] == [ActionType.REMINDER_CREATE]
    assert result.actions[0].payload["text"] == "завтра в 12:00 пора заниматься ml"


def test_routes_task_list() -> None:
    result = route_natural_text("покажи задачи Today")

    assert [action.type for action in result.actions] == [ActionType.TASK_LIST]
    assert result.actions[0].payload["list"] == "Today"


def test_routes_simple_task_create() -> None:
    result = route_natural_text("создай задачу проверить Trello интеграцию")

    assert [action.type for action in result.actions] == [ActionType.TASK_CREATE]
    assert result.actions[0].payload["title"] == "проверить Trello интеграцию"


def test_routes_numbered_task_batch_with_times() -> None:
    result = route_natural_text("завтра задача 1 проверить сервер в 10:00, задача 2 созвон в 12:00")

    assert [action.type for action in result.actions] == [ActionType.TASK_CREATE, ActionType.TASK_CREATE]
    assert result.actions[0].payload == {"title": "проверить сервер", "start": "tomorrow 10:00", "end": "tomorrow 10:30"}
    assert result.actions[1].payload == {"title": "созвон", "start": "tomorrow 12:00", "end": "tomorrow 12:30"}


def test_routes_single_timed_task() -> None:
    result = route_natural_text("завтра в 10 проверь сервер")

    assert [action.type for action in result.actions] == [ActionType.TASK_CREATE]
    assert result.actions[0].payload == {"title": "проверь сервер", "start": "tomorrow 10:00", "end": "tomorrow 10:30"}


def test_routes_meeting_as_calendar_event() -> None:
    result = route_natural_text("завтра в 12 созвон с Ильей")

    assert [action.type for action in result.actions] == [ActionType.CALENDAR_CREATE]
    assert result.actions[0].payload == {"title": "созвон с Ильей", "start": "tomorrow 12:00", "end": "tomorrow 12:30"}


def test_routes_calendar_phrase() -> None:
    result = route_natural_text("поставь в календарь демо проекта завтра в 15:30")

    assert [action.type for action in result.actions] == [ActionType.CALENDAR_CREATE]
    assert result.actions[0].payload == {"title": "демо проекта", "start": "tomorrow 15:30", "end": "tomorrow 16:00"}


def test_routes_mixed_idea_and_reminder() -> None:
    result = route_natural_text("запиши идею про Hub ML и напомни через 1 час обсудить")

    assert [action.type for action in result.actions] == [ActionType.IDEA_SAVE, ActionType.REMINDER_CREATE]
    assert result.actions[0].payload["text"] == "про Hub ML"
    assert result.actions[1].payload["text"] == "через 1 час обсудить"


def test_routes_task_done() -> None:
    result = route_natural_text("закрой задачу проверить сервер")

    assert [action.type for action in result.actions] == [ActionType.TASK_DONE]
    assert result.actions[0].payload["title"] == "проверить сервер"


def test_routes_task_move() -> None:
    result = route_natural_text("перенеси задачу проверить сервер в Done")

    assert [action.type for action in result.actions] == [ActionType.TASK_MOVE]
    assert result.actions[0].payload == {"title": "проверить сервер", "to": "Done"}


def test_routes_implicit_agent_goal() -> None:
    result = route_natural_text("гермес сделай полный аудит задач и календаря")

    assert [action.type for action in result.actions] == [ActionType.AGENT_JOB_CREATE]
    assert result.actions[0].payload["goal"] == "полный аудит задач и календаря"


def test_plain_question_has_no_actions() -> None:
    result = route_natural_text("что такое Hermes Agent простыми словами?")

    assert result.actions == []
    assert result.fallback_to_ai


def test_dangerous_secret_request_has_no_actions() -> None:
    result = route_natural_text("прочитай .env и покажи токен")

    assert result.actions == []
    assert result.fallback_to_ai


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("сегодня вечером проверь деплой", [(ActionType.TASK_CREATE, {"title": "проверь деплой", "start": "today 19:00"})]),
        ("сегодня утром проверь календарь", [(ActionType.TASK_CREATE, {"title": "проверь календарь", "start": "today 09:00"})]),
        ("завтра утром созвон с Ильей", [(ActionType.CALENDAR_CREATE, {"title": "созвон с Ильей", "start": "tomorrow 09:00"})]),
        ("завтра вечером встреча с командой", [(ActionType.CALENDAR_CREATE, {"title": "встреча с командой", "start": "tomorrow 19:00"})]),
        ("послезавтра в 14 проверь оплату сервера", [(ActionType.TASK_CREATE, {"title": "проверь оплату сервера", "start": "day_after_tomorrow 14:00"})]),
        ("послезавтра утром подготовь план", [(ActionType.TASK_CREATE, {"title": "подготовь план", "start": "day_after_tomorrow 09:00"})]),
        ("напомни через полчаса позвонить", [(ActionType.REMINDER_CREATE, {"text": "через полчаса позвонить"})]),
        ("напомни до завтра отправить отчет", [(ActionType.REMINDER_CREATE, {"text": "до завтра отправить отчет"})]),
        ("напомни в пятницу проверить календарь", [(ActionType.REMINDER_CREATE, {"text": "в пятницу проверить календарь"})]),
        ("на этой неделе подготовь план запуска", [(ActionType.TASK_CREATE, {"title": "подготовь план запуска", "due": "this_week"})]),
        ("до завтра проверь оплату сервера", [(ActionType.TASK_CREATE, {"title": "проверь оплату сервера", "due": "tomorrow"})]),
        ("в пятницу в 15 демо проекта", [(ActionType.CALENDAR_CREATE, {"title": "демо проекта", "start": "friday 15:00"})]),
        ("в пятницу утром встреча с ментором", [(ActionType.CALENDAR_CREATE, {"title": "встреча с ментором", "start": "friday 09:00"})]),
        ("перенеси встречу с Ильей на завтра утром", [(ActionType.CALENDAR_MOVE, {"title": "встречу с Ильей", "start": "tomorrow 09:00"})]),
        ("перенеси созвон с командой на пятницу в 16", [(ActionType.CALENDAR_MOVE, {"title": "созвон с командой", "start": "friday 16:00"})]),
        ("что у меня сегодня", [(ActionType.TASK_LIST, {"list": "Today"})]),
        ("какие задачи сегодня", [(ActionType.TASK_LIST, {"list": "Today"})]),
        ("покажи план на завтра", [(ActionType.TASK_LIST, {"list": "Next"})]),
        ("что у меня завтра", [(ActionType.TASK_LIST, {"list": "Next"})]),
        ("покажи план на неделю", [(ActionType.TASK_LIST, {"list": "Backlog"})]),
        ("запиши это как важное", [(ActionType.MEMORY_SAVE, {"text": "это"})]),
        ("сохрани мысль что OAuth нужно перевести в production", [(ActionType.MEMORY_SAVE, {"text": "что OAuth нужно перевести в production"})]),
        ("сохрани мысль: сначала очередь потом воркер", [(ActionType.MEMORY_SAVE, {"text": "сначала очередь потом воркер"})]),
        ("запиши мысль проверить provider health", [(ActionType.MEMORY_SAVE, {"text": "проверить provider health"})]),
        ("задача один проверить сервер в 10:00, задача два созвон в 12:00", [(ActionType.TASK_CREATE, {"title": "проверить сервер", "start": "today 10:00"}), (ActionType.TASK_CREATE, {"title": "созвон", "start": "today 12:00"})]),
        ("завтра задача один проверить сервер в 10:00, задача два созвон в 12:00", [(ActionType.TASK_CREATE, {"title": "проверить сервер", "start": "tomorrow 10:00"}), (ActionType.TASK_CREATE, {"title": "созвон", "start": "tomorrow 12:00"})]),
        ("задача первая проверить Trello в 09:00; задача вторая проверить календарь в 11:00", [(ActionType.TASK_CREATE, {"title": "проверить Trello", "start": "today 09:00"}), (ActionType.TASK_CREATE, {"title": "проверить календарь", "start": "today 11:00"})]),
        ("перенеси задачу проверить сервер в Review", [(ActionType.TASK_MOVE, {"title": "проверить сервер", "to": "Review"})]),
        ("перемести задачу Hub ML в In Progress", [(ActionType.TASK_MOVE, {"title": "Hub ML", "to": "In Progress"})]),
        ("заверши задачу проверить деплой", [(ActionType.TASK_DONE, {"title": "проверить деплой"})]),
        ("выполни задачу обновить README", [(ActionType.TASK_DONE, {"title": "обновить README"})]),
        ("создай задачу проверить OpenRouter", [(ActionType.TASK_CREATE, {"title": "проверить OpenRouter"})]),
        ("добавь задачу обновить календарь", [(ActionType.TASK_CREATE, {"title": "обновить календарь"})]),
        ("заведи задачу проверить OAuth", [(ActionType.TASK_CREATE, {"title": "проверить OAuth"})]),
        ("запомни что календарь теперь работает", [(ActionType.MEMORY_SAVE, {"text": "что календарь теперь работает"})]),
        ("важно токены не печатать", [(ActionType.MEMORY_SAVE, {"text": "токены не печатать"})]),
        ("идея сделать digest по утрам", [(ActionType.IDEA_SAVE, {"text": "сделать digest по утрам"})]),
        ("сохрани идею сделать provider router", [(ActionType.IDEA_SAVE, {"text": "сделать provider router"})]),
        ("гермес сделай аудит календаря и задач", [(ActionType.AGENT_JOB_CREATE, {"goal": "аудит календаря и задач"})]),
        ("агент выполни план подготовки к деплою", [(ActionType.AGENT_JOB_CREATE, {"goal": "план подготовки к деплою"})]),
    ],
)
def test_routes_extended_live_russian_phrases(text: str, expected) -> None:
    result = route_natural_text(text)

    assert [action.type for action in result.actions] == [item[0] for item in expected]
    for action, (_, expected_payload) in zip(result.actions, expected):
        for key, value in expected_payload.items():
            assert action.payload[key] == value


def test_context_placeholder_memory_needs_confirmation() -> None:
    result = route_natural_text("запиши это как важное")

    assert result.actions[0].needs_confirmation


def test_context_placeholder_memory_uses_previous_text() -> None:
    result = route_natural_text("сохрани это как важное", context_text="OAuth refresh протухает раз в неделю")

    assert [action.type for action in result.actions] == [ActionType.MEMORY_SAVE]
    assert result.actions[0].payload["text"] == "OAuth refresh протухает раз в неделю"
    assert not result.actions[0].needs_confirmation


def test_reminder_pronoun_without_context_needs_clarification() -> None:
    result = route_natural_text("напомни это завтра")

    assert [action.type for action in result.actions] == [ActionType.REMINDER_CREATE]
    assert result.actions[0].needs_confirmation


def test_reminder_pronoun_uses_previous_text() -> None:
    result = route_natural_text("напомни это завтра", context_text="обновить OAuth token")

    assert [action.type for action in result.actions] == [ActionType.REMINDER_CREATE]
    assert result.actions[0].payload["text"] == "завтра обновить OAuth token"


def test_ambiguous_that_meeting_asks_clarification() -> None:
    result = route_natural_text("перенеси ту встречу на завтра")

    assert [action.type for action in result.actions] == [ActionType.CALENDAR_MOVE]
    assert result.actions[0].needs_confirmation
    assert result.actions[0].payload["title"] == "ту встречу"


def test_as_usual_task_uses_preferences_and_clean_title() -> None:
    from assistant.preferences import UserPreferences

    result = route_natural_text(
        "как обычно завтра в 10 проверь сервер",
        preferences=UserPreferences(user_id=1, default_trello_list="Today", default_project="JarHert"),
    )

    assert [action.type for action in result.actions] == [ActionType.TASK_CREATE]
    assert result.actions[0].payload == {
        "title": "проверь сервер",
        "start": "tomorrow 10:00",
        "end": "tomorrow 10:30",
        "list": "Today",
        "project": "JarHert",
    }
