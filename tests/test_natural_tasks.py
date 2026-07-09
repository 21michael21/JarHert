from __future__ import annotations

from datetime import date

from assistant.natural_tasks import parse_natural_task_batch


def test_parse_numbered_tasks_with_times() -> None:
    tasks = parse_natural_task_batch(
        "завтра задача 1 проверить сервер в 10:00, задача 2 созвон с Ильей в 12:30",
        today=date(2026, 7, 9),
    )

    assert [task.title for task in tasks] == ["проверить сервер", "созвон с Ильей"]
    assert tasks[0].start == "tomorrow 10:00"
    assert tasks[0].end == "tomorrow 10:30"
    assert tasks[1].start == "tomorrow 12:30"
    assert tasks[1].end == "tomorrow 13:00"


def test_parse_plain_numbered_lines() -> None:
    tasks = parse_natural_task_batch("1) Hub ML в 09:00\n2) Telegram Library в 11:00")

    assert [task.title for task in tasks] == ["Hub ML", "Telegram Library"]
    assert tasks[0].start == "today 09:00"
    assert tasks[1].start == "today 11:00"


def test_parse_explicit_date() -> None:
    tasks = parse_natural_task_batch("10.07.2026 задача 1 проверить календарь в 10:00")

    assert tasks[0].start == "2026-07-10 10:00"
