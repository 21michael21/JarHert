from __future__ import annotations

from datetime import date

from assistant.action_schema import ActionType
from assistant.voice_inbox import parse_voice_inbox


def test_voice_inbox_splits_clear_events_from_questions_without_an_llm() -> None:
    parsed = parse_voice_inbox(
        "Бро, привет. Мне нужно отправить расписание на завтра: у меня встреча в 13:00. "
        "Через неделю у меня встреча в 13:00 в этот же день. "
        "Ещё оцени фильм «Пленница». Как думаешь, норм или нет?",
        today=date(2026, 7, 13),
    )

    assert [action.type for action in parsed.actions] == [
        ActionType.CALENDAR_CREATE,
        ActionType.CALENDAR_CREATE,
    ]
    assert [action.payload for action in parsed.actions] == [
        {"title": "Встреча", "start": "2026-07-14 13:00", "end": "2026-07-14 14:00"},
        {"title": "Встреча", "start": "2026-07-20 13:00", "end": "2026-07-20 14:00"},
    ]
    assert parsed.followups == (
        "Кому отправить расписание на завтра?",
        "По фильму «Пленница» скинь год или ссылку: под этим названием есть разные фильмы.",
    )


def test_voice_inbox_keeps_other_clear_actions_in_the_same_plan() -> None:
    parsed = parse_voice_inbox(
        "Завтра в 10 встреча с Ильёй. Потом напомни завтра в 12 заняться ML. "
        "И сохрани идею сделать голосовой inbox.",
        today=date(2026, 7, 13),
    )

    assert [action.type for action in parsed.actions] == [
        ActionType.CALENDAR_CREATE,
        ActionType.REMINDER_CREATE,
        ActionType.IDEA_SAVE,
    ]
    assert parsed.actions[0].payload == {
        "title": "Встреча с Ильёй",
        "start": "2026-07-14 10:00",
        "end": "2026-07-14 11:00",
    }
    assert parsed.actions[1].payload["text"] == "завтра в 12 заняться ML"
    assert parsed.actions[2].payload["text"] == "сделать голосовой inbox"
    assert parsed.followups == ()


def test_voice_inbox_moves_the_end_to_the_next_day_after_midnight() -> None:
    parsed = parse_voice_inbox(
        "Сегодня в 23:30 встреча с командой.",
        today=date(2026, 7, 13),
    )

    assert parsed.actions[0].payload == {
        "title": "Встреча с командой",
        "start": "2026-07-13 23:30",
        "end": "2026-07-14 00:30",
    }
