from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ParsedReminder:
    remind_at: datetime
    text: str


RELATIVE_RE = re.compile(
    r"^через\s+(?P<num>\d+)\s+(?P<unit>минут[уы]?|мин|час(?:а|ов)?|дн(?:я|ей)?)\s+(?P<text>.+)$",
    re.IGNORECASE,
)
HALF_HOUR_RE = re.compile(r"^через\s+полчаса\s+(?P<text>.+)$", re.IGNORECASE)
UNTIL_TOMORROW_RE = re.compile(r"^до\s+завтра\s+(?P<text>.+)$", re.IGNORECASE)
WEEKDAY_RE = re.compile(
    r"^в\s+(?P<weekday>понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\s+(?P<text>.+)$",
    re.IGNORECASE,
)
DATE_TIME_RE = re.compile(
    r"^(?P<date>сегодня|завтра|послезавтра)\s+(?:в\s+)?(?:час(?:ов|а)?\s+)?"
    r"(?P<time>\d{1,2}(?::\d{2})?)\s*(?:утра|дня|вечера|ночи)?\s+(?P<text>.+)$",
    re.IGNORECASE,
)
DATE_DEFAULT_RE = re.compile(r"^(?P<date>сегодня|завтра|послезавтра)\s+(?P<text>.+)$", re.IGNORECASE)

ABSOLUTE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{1,2}:\d{2})\s+(?P<text>.+)$",
    re.IGNORECASE,
)


def parse_reminder(
    text: str,
    *,
    now: datetime | None = None,
    default_time: str = "09:00",
) -> ParsedReminder | None:
    value = (text or "").strip()
    if not value:
        return None

    base = now or datetime.now(timezone.utc)
    half_hour = HALF_HOUR_RE.match(value)
    if half_hour:
        return ParsedReminder(remind_at=base + timedelta(minutes=30), text=half_hour.group("text").strip())

    relative = RELATIVE_RE.match(value)
    if relative:
        amount = int(relative.group("num"))
        unit = relative.group("unit").lower()
        if unit.startswith("мин"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("час"):
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        return ParsedReminder(remind_at=base + delta, text=relative.group("text").strip())

    until_tomorrow = UNTIL_TOMORROW_RE.match(value)
    if until_tomorrow:
        hour, minute = _clock_parts(default_time)
        remind_at = (base + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return ParsedReminder(remind_at=remind_at, text=until_tomorrow.group("text").strip())

    weekday = WEEKDAY_RE.match(value)
    if weekday:
        hour, minute = _clock_parts(default_time)
        remind_at = _next_weekday(base, weekday.group("weekday")).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return ParsedReminder(remind_at=remind_at, text=weekday.group("text").strip())

    date_time = DATE_TIME_RE.match(value)
    if date_time:
        hour, minute = _clock_parts(_normalize_clock(date_time.group("time")))
        remind_at = (base + timedelta(days=_date_offset(date_time.group("date")))).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        return ParsedReminder(remind_at=remind_at, text=date_time.group("text").strip())

    date_default = DATE_DEFAULT_RE.match(value)
    if date_default:
        hour, minute = _clock_parts(default_time)
        remind_at = (base + timedelta(days=_date_offset(date_default.group("date")))).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        return ParsedReminder(remind_at=remind_at, text=date_default.group("text").strip())

    absolute = ABSOLUTE_RE.match(value)
    if absolute:
        raw = f"{absolute.group('date')} {absolute.group('time')}"
        remind_at = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=base.tzinfo)
        return ParsedReminder(remind_at=remind_at, text=absolute.group("text").strip())

    return None


def _next_weekday(base: datetime, raw_weekday: str) -> datetime:
    weekdays = {
        "понедельник": 0,
        "вторник": 1,
        "среду": 2,
        "четверг": 3,
        "пятницу": 4,
        "субботу": 5,
        "воскресенье": 6,
    }
    target = weekdays[raw_weekday.lower()]
    days_ahead = (target - base.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return base + timedelta(days=days_ahead)


def _clock_parts(value: str) -> tuple[int, int]:
    try:
        hours, minutes = value.split(":", 1)
        return int(hours), int(minutes)
    except (AttributeError, ValueError):
        return 9, 0


def _normalize_clock(value: str) -> str:
    if ":" in value:
        hours, minutes = value.split(":", 1)
        return f"{int(hours):02d}:{minutes}"
    return f"{int(value):02d}:00"


def _date_offset(value: str) -> int:
    lowered = value.lower()
    if lowered == "послезавтра":
        return 2
    if lowered == "завтра":
        return 1
    return 0
