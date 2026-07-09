from __future__ import annotations

import re


_KEY_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "trello_card_id",
        re.compile(r"\b(?:trello_)?(?:card_id|idCard)\s*[:=]\s*([A-Za-z0-9_-]{6,64})\b", re.IGNORECASE),
    ),
    (
        "calendar_event_id",
        re.compile(
            r"\b(?:calendar_)?(?:event_id|google_event_id)\s*[:=]\s*([A-Za-z0-9_-]{6,128})\b",
            re.IGNORECASE,
        ),
    ),
)

_TRELLO_URL_RE = re.compile(r"https://trello\.com/c/[A-Za-z0-9_-]+[^\s)]*", re.IGNORECASE)
_CALENDAR_URL_RE = re.compile(r"https://calendar\.google\.com/[^\s)]*", re.IGNORECASE)


def extract_tool_result_ids(output: str) -> dict[str, str]:
    text = output or ""
    meta: dict[str, str] = {}
    for key, pattern in _KEY_VALUE_PATTERNS:
        match = pattern.search(text)
        if match:
            meta[key] = _clean(match.group(1))

    trello_url = _TRELLO_URL_RE.search(text)
    if trello_url:
        meta["trello_card_url"] = _clean(trello_url.group(0))

    calendar_url = _CALENDAR_URL_RE.search(text)
    if calendar_url:
        meta["calendar_event_url"] = _clean(calendar_url.group(0))

    return {key: value for key, value in meta.items() if value}


def compact_result_meta(meta: dict[str, str], *, limit: int = 120) -> str:
    parts = []
    for key in sorted(meta):
        value = _clean(str(meta[key]))
        if len(value) > limit:
            value = value[: limit - 1].rstrip() + "…"
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _clean(value: str) -> str:
    return value.strip().rstrip(".,;")
