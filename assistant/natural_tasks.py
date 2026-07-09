from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


TIME_RE = re.compile(r"(?<![\d.])(?:\b(?:в|на|к)\s*)?((?:[01]?\d|2[0-3])[:.][0-5]\d)(?![\d.])", re.IGNORECASE)
EXPLICIT_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b")
TASK_SPLIT_RE = re.compile(
    r"(?:^|[\n,;])\s*(?:задача\s*)?\d+[\).:\-]?\s*",
    re.IGNORECASE,
)
WORD_TASK_SPLIT_RE = re.compile(
    r"(?:^|[\n,;]\s*|\b)(?:задача\s+)(?:\d+|один|два|три|четыре|пять|первая|вторая|третья|четвертая|четвёртая|пятая)[\).:\-]?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NaturalTask:
    title: str
    start: str | None = None
    end: str | None = None


def parse_natural_task_batch(
    text: str,
    *,
    default_duration_minutes: int = 30,
    today: date | None = None,
) -> list[NaturalTask]:
    raw = (text or "").strip()
    if not raw:
        return []
    items = [_parse_item(chunk, raw, default_duration_minutes=default_duration_minutes, today=today) for chunk in _split_items(raw)]
    return [item for item in items if item is not None]


def _split_items(text: str) -> list[str]:
    normalized = _drop_global_prefix(text.strip())
    parts = [part.strip() for part in TASK_SPLIT_RE.split(normalized) if part.strip()]
    if len(parts) >= 2:
        return parts
    parts = [part.strip() for part in WORD_TASK_SPLIT_RE.split(normalized) if part.strip()]
    if len(parts) >= 2:
        return parts
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines
    return [normalized]


def _drop_global_prefix(text: str) -> str:
    return re.sub(
        r"^(?:сегодня|завтра|послезавтра|\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\s+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _parse_item(
    item: str,
    context: str,
    *,
    default_duration_minutes: int,
    today: date | None,
) -> NaturalTask | None:
    time_match = TIME_RE.search(item)
    title = item
    start = None
    end = None
    if time_match:
        raw_time = time_match.group(1).replace(".", ":")
        title = (item[: time_match.start()] + item[time_match.end() :]).strip(" .,-—")
        start = _datetime_value(context, raw_time, today=today)
        end = _datetime_value(context, _add_minutes(raw_time, default_duration_minutes), today=today)
    title = _clean_title(title)
    if not title:
        return None
    return NaturalTask(title=title, start=start, end=end)


def _datetime_value(context: str, clock: str, *, today: date | None) -> str:
    lowered = context.lower()
    if "послезавтра" in lowered:
        return f"{_today(today) + timedelta(days=2)} {clock}"
    if "завтра" in lowered:
        return f"tomorrow {clock}"
    explicit = EXPLICIT_DATE_RE.search(context)
    if explicit:
        return f"{_normalize_date(explicit.group(1))} {clock}"
    if "сегодня" in lowered:
        return f"today {clock}"
    return f"today {clock}"


def _add_minutes(clock: str, minutes: int) -> str:
    hours, raw_minutes = [int(part) for part in clock.split(":", 1)]
    total = hours * 60 + raw_minutes + minutes
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _normalize_date(value: str) -> str:
    if "-" in value:
        return value
    day, month, year = [int(part) for part in value.split(".")]
    return f"{year:04d}-{month:02d}-{day:02d}"


def _clean_title(value: str) -> str:
    title = re.sub(r"\b(?:сегодня|завтра|послезавтра)\b", "", value, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:в|на|к)\s*$", "", title, flags=re.IGNORECASE)
    return " ".join(title.strip(" .,-—:").split())


def _today(value: date | None) -> date:
    return value or date.today()
