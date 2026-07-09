from datetime import datetime, timezone

from reminders.parser import parse_reminder


def test_parse_relative_hours() -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    reminder = parse_reminder("через 2 часа позвонить", now=now)
    assert reminder is not None
    assert reminder.remind_at.hour == 12
    assert reminder.text == "позвонить"


def test_parse_absolute_datetime() -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    reminder = parse_reminder("2026-07-09 09:30 проверить деплой", now=now)
    assert reminder is not None
    assert reminder.remind_at.isoformat() == "2026-07-09T09:30:00+00:00"
    assert reminder.text == "проверить деплой"


def test_unknown_reminder_returns_none() -> None:
    assert parse_reminder("когда-нибудь потом") is None


def test_parse_half_hour_reminder() -> None:
    now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)

    reminder = parse_reminder("через полчаса позвонить", now=now)

    assert reminder is not None
    assert reminder.remind_at.isoformat() == "2026-07-09T10:30:00+00:00"
    assert reminder.text == "позвонить"


def test_parse_until_tomorrow_reminder() -> None:
    now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)

    reminder = parse_reminder("до завтра отправить отчет", now=now)

    assert reminder is not None
    assert reminder.remind_at.isoformat() == "2026-07-10T09:00:00+00:00"
    assert reminder.text == "отправить отчет"


def test_parse_weekday_reminder() -> None:
    now = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)

    reminder = parse_reminder("в пятницу проверить календарь", now=now)

    assert reminder is not None
    assert reminder.remind_at.isoformat() == "2026-07-10T09:00:00+00:00"
    assert reminder.text == "проверить календарь"
