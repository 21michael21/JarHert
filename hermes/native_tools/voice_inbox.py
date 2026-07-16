"""Small, local preparation layer for noisy Telegram voice transcripts."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .database import open_personal_os_database


@dataclass(frozen=True)
class VoiceInboxPrepared:
    text: str
    mode: str
    replacements: tuple[str, ...]


@dataclass(frozen=True)
class VoiceVocabularyEntry:
    id: int
    spoken: str
    canonical: str


class VoiceVocabularyStore:
    """Owner-curated vocabulary; it corrects terms but never invents actions."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(self, *, spoken: str, canonical: str) -> VoiceVocabularyEntry:
        source = _term(spoken, "Произнесённый вариант")
        target = _term(canonical, "Правильное написание")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO voice_vocabulary(spoken, spoken_key, canonical)
                VALUES (?, ?, ?)
                ON CONFLICT(spoken_key) DO UPDATE SET spoken = excluded.spoken, canonical = excluded.canonical
                """,
                (source, source.casefold(), target),
            )
            row = connection.execute(
                "SELECT id, spoken, canonical FROM voice_vocabulary WHERE spoken_key = ?",
                (source.casefold(),),
            ).fetchone()
        return _entry_from_row(row)

    def list(self, *, limit: int = 100) -> tuple[VoiceVocabularyEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, spoken, canonical FROM voice_vocabulary ORDER BY spoken_key LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return tuple(_entry_from_row(row) for row in rows)

    def prepare(self, transcript: str) -> VoiceInboxPrepared:
        original = _transcript(transcript)
        prepared = re.sub(r"\s+", " ", original).strip()
        applied: list[str] = []
        for entry in sorted(self.list(), key=lambda item: len(item.spoken), reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(entry.spoken)}(?!\w)", re.IGNORECASE)
            prepared, count = pattern.subn(entry.canonical, prepared)
            if count:
                applied.append(f"{entry.spoken} -> {entry.canonical}")
        return VoiceInboxPrepared(
            text=prepared,
            mode=_voice_mode(prepared),
            replacements=tuple(applied),
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_vocabulary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spoken TEXT NOT NULL,
                    spoken_key TEXT NOT NULL UNIQUE,
                    canonical TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path, autocommit=True)


def _voice_mode(text: str) -> str:
    """Classify shape only; the LLM still decides the actual action plan."""
    normalized = text.casefold()
    intents = {
        "reminder": ("напомни", "напоминан"),
        "note": ("сохрани", "запиши", "заметк"),
        "task": ("задач",),
        "meeting": ("встреч", "созвон", "календар"),
        "message": ("отправ", "напиши"),
        "move": ("перенеси",),
    }
    detected = {name for name, markers in intents.items() if any(marker in normalized for marker in markers)}
    if len(detected) == 1 and len(text) <= 280:
        return "command"
    if detected:
        return "inbox"
    return "dictation"


def _term(value: str, label: str) -> str:
    clean = " ".join(str(value or "").split())
    if not clean or len(clean) > 160:
        raise ValueError(f"{label} должен быть от 1 до 160 символов.")
    return clean


def _transcript(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 24_000:
        raise ValueError("Расшифровка должна быть от 1 до 24000 символов.")
    return clean


def _entry_from_row(row: sqlite3.Row) -> VoiceVocabularyEntry:
    return VoiceVocabularyEntry(id=int(row["id"]), spoken=str(row["spoken"]), canonical=str(row["canonical"]))
