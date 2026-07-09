from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone


TIME_RE = re.compile(r"\b(?P<time>(?:[01]?\d|2[0-3])(?::[0-5]\d)?)\b")


@dataclass(frozen=True)
class UserPreferences:
    user_id: int
    timezone: str = "UTC"
    default_trello_list: str = "Inbox"
    default_project: str | None = None
    default_reminder_time: str = "09:00"
    morning_time: str = "09:00"
    evening_time: str = "19:00"
    preferred_response_style: str = "concise"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class PreferenceUpdate:
    updates: dict[str, str | None]
    message: str


class InMemoryPreferenceStore:
    def __init__(self) -> None:
        self._items: dict[int, UserPreferences] = {}

    def get(self, user_id: int) -> UserPreferences:
        if user_id not in self._items:
            now = datetime.now(timezone.utc)
            self._items[user_id] = UserPreferences(user_id=user_id, created_at=now, updated_at=now)
        return self._items[user_id]

    def update(self, user_id: int, **updates) -> UserPreferences:
        current = self.get(user_id)
        clean = {key: value for key, value in updates.items() if value is not None}
        updated = replace(current, **clean, updated_at=datetime.now(timezone.utc))
        self._items[user_id] = updated
        return updated


def parse_preference_update(text: str) -> PreferenceUpdate | None:
    value = " ".join((text or "").strip().split())
    lowered = value.lower()
    if not value or value.startswith("/"):
        return None

    match = re.match(r"^по\s+умолчанию\s+задачи\s+в\s+(?P<list>.+)$", value, re.IGNORECASE)
    if match:
        list_name = match.group("list").strip()
        return PreferenceUpdate(
            {"default_trello_list": list_name},
            f"Сохранил настройку: задачи по умолчанию идут в {list_name}.",
        )

    match = re.match(r"^по\s+умолчанию\s+проект\s+(?P<project>.+)$", value, re.IGNORECASE)
    if match:
        project = match.group("project").strip()
        return PreferenceUpdate(
            {"default_project": project},
            f"Сохранил настройку: проект по умолчанию — {project}.",
        )

    match = re.match(r"^вечером\s+это\s+(?P<time>.+)$", value, re.IGNORECASE)
    if match:
        clock = _normalize_time(match.group("time"))
        if clock:
            return PreferenceUpdate(
                {"evening_time": clock},
                f"Сохранил настройку: вечером — это {clock}.",
            )

    match = re.match(r"^утром\s+это\s+(?P<time>.+)$", value, re.IGNORECASE)
    if match:
        clock = _normalize_time(match.group("time"))
        if clock:
            return PreferenceUpdate(
                {"morning_time": clock},
                f"Сохранил настройку: утром — это {clock}.",
            )

    match = re.match(r"^напоминания\s+по\s+умолчанию\s+в\s+(?P<time>.+)$", value, re.IGNORECASE)
    if match:
        clock = _normalize_time(match.group("time"))
        if clock:
            return PreferenceUpdate(
                {"default_reminder_time": clock},
                f"Сохранил настройку: напоминания по умолчанию в {clock}.",
            )

    match = re.match(r"^часовой\s+пояс\s+(?P<tz>[A-Za-z_./+-]+)$", value, re.IGNORECASE)
    if match:
        timezone_name = match.group("tz").strip()
        return PreferenceUpdate(
            {"timezone": timezone_name},
            f"Сохранил настройку: часовой пояс {timezone_name}.",
        )

    if lowered in {"пиши короче", "отвечай короче", "короче"}:
        return PreferenceUpdate(
            {"preferred_response_style": "short"},
            "Сохранил настройку: буду отвечать короче.",
        )
    if lowered in {"пиши подробнее", "отвечай подробнее"}:
        return PreferenceUpdate(
            {"preferred_response_style": "detailed"},
            "Сохранил настройку: буду отвечать подробнее.",
        )
    return None


def _normalize_time(value: str) -> str | None:
    match = TIME_RE.search(value or "")
    if not match:
        return None
    raw = match.group("time")
    if ":" in raw:
        hours, minutes = raw.split(":", 1)
        return f"{int(hours):02d}:{minutes}"
    return f"{int(raw):02d}:00"
