from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import open_personal_os_database


CONFIRMATIONS_REQUIRED = 3
ALLOWED_SKILL_TOOLS = frozenset(
    {
        "calendar",
        "contact_messaging",
        "event_monitors",
        "files",
        "git",
        "personal_memory",
        "personal_operating_center",
        "reminders",
        "telegram_delivery",
        "tests",
        "trello",
        "web_search",
    }
)
_WORKFLOW_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_SENSITIVE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|\d{8,12}:[A-Za-z0-9_-]{20,}|"
    r"https?://\S+|(?:/Users|/home)/\S+|\b[^\s@]+@[^\s@]+\.[^\s@]+\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SkillCandidate:
    workflow_key: str
    title: str
    skill_name: str
    confirmation_count: int
    status: str
    skill_markdown: str


class SkillDistiller:
    """Count confirmed repeats and produce inert, reviewable skill drafts."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def observe(
        self,
        *,
        workflow_key: str,
        title: str,
        steps: list[dict[str, Any]],
        idempotency_key: str,
        success: bool,
        confirmed: bool,
    ) -> SkillCandidate:
        key = _validate_workflow_key(workflow_key)
        clean_title = _required(title, "Title")[:80]
        clean_steps = _validate_steps(steps)
        steps_json = _canonical_json(clean_steps)
        steps_hash = hashlib.sha256(steps_json.encode("utf-8")).hexdigest()
        observation_key = _required(idempotency_key, "Idempotency key")[:180]

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                "SELECT workflow_key FROM skill_observations WHERE idempotency_key = ?",
                (observation_key,),
            ).fetchone()
            if replay is not None:
                candidate = self._get_candidate(connection, replay["workflow_key"])
                connection.commit()
                return candidate

            row = connection.execute(
                "SELECT * FROM skill_candidates WHERE workflow_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO skill_candidates(
                        workflow_key, title, skill_name, steps_json, steps_hash,
                        confirmation_count, status, skill_markdown
                    ) VALUES (?, ?, ?, ?, ?, 0, 'observing', ?)
                    """,
                    (
                        key,
                        clean_title,
                        f"learned-{key}",
                        steps_json,
                        steps_hash,
                        _build_skill_markdown(key, clean_title, clean_steps),
                    ),
                )
            elif row["steps_hash"] != steps_hash:
                connection.rollback()
                raise ValueError("Для этого workflow key уже сохранена другая процедура.")

            counts = bool(success and confirmed)
            connection.execute(
                """
                INSERT INTO skill_observations(
                    workflow_key, idempotency_key, success, confirmed, counted
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (key, observation_key, int(success), int(confirmed), int(counts)),
            )
            if counts:
                connection.execute(
                    """
                    UPDATE skill_candidates
                    SET confirmation_count = confirmation_count + 1,
                        status = CASE
                            WHEN confirmation_count + 1 >= ? THEN 'ready_for_review'
                            ELSE 'observing'
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workflow_key = ?
                    """,
                    (CONFIRMATIONS_REQUIRED, key),
                )
            candidate = self._get_candidate(connection, key)
            connection.commit()
            return candidate

    def get_candidate(self, workflow_key: str) -> SkillCandidate:
        with self._connect() as connection:
            return self._get_candidate(connection, _validate_workflow_key(workflow_key))

    def list_candidates(self, *, ready_only: bool = False) -> list[SkillCandidate]:
        query = "SELECT * FROM skill_candidates"
        if ready_only:
            query += " WHERE status = 'ready_for_review'"
        query += " ORDER BY updated_at DESC, workflow_key"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_candidate_from_row(row) for row in rows]

    def mark_staged(self, workflow_key: str) -> SkillCandidate:
        key = _validate_workflow_key(workflow_key)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE skill_candidates SET status = 'staged', updated_at = CURRENT_TIMESTAMP
                WHERE workflow_key = ? AND status = 'ready_for_review'
                """,
                (key,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Skill candidate не готов к staging.")
        return self.get_candidate(key)

    def _get_candidate(self, connection: sqlite3.Connection, key: str) -> SkillCandidate:
        row = connection.execute(
            "SELECT * FROM skill_candidates WHERE workflow_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise ValueError("Skill candidate не найден.")
        return _candidate_from_row(row)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS skill_candidates (
                    workflow_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    skill_name TEXT NOT NULL UNIQUE,
                    steps_json TEXT NOT NULL,
                    steps_hash TEXT NOT NULL,
                    confirmation_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'observing',
                    skill_markdown TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS skill_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_key TEXT NOT NULL REFERENCES skill_candidates(workflow_key),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    success INTEGER NOT NULL,
                    confirmed INTEGER NOT NULL,
                    counted INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path, timeout_seconds=5)


def _validate_steps(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(steps, list) or not 2 <= len(steps) <= 12:
        raise ValueError("Skill procedure должна содержать от 2 до 12 шагов.")
    clean: list[dict[str, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Каждый skill step должен быть JSON-объектом.")
        tool = str(step.get("tool") or "").strip()
        if tool not in ALLOWED_SKILL_TOOLS:
            raise ValueError(f"Tool '{tool}' отсутствует в skill allowlist.")
        summary = _redact(_required(str(step.get("summary") or ""), "Step summary"))[:180]
        clean.append({"tool": tool, "summary": summary})
    return clean


def _build_skill_markdown(key: str, title: str, steps: list[dict[str, str]]) -> str:
    lines = [
        "---",
        f"name: learned-{key}",
        f"description: Повторяемая процедура: {_redact(title)[:60]}",
        "---",
        "",
        f"# {_redact(title)}",
        "",
        "## Workflow",
        "",
    ]
    lines.extend(
        f"{index}. `{step['tool']}`: {step['summary']}"
        for index, step in enumerate(steps, start=1)
    )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Confirm tool success before reporting completion.",
            "- Keep one confirmation for the complete plan.",
            "- Do not store credentials, private paths, or raw conversation text.",
            "- Stop and ask one short question when a required identifier is ambiguous.",
            "",
        ]
    )
    return "\n".join(lines)


def _candidate_from_row(row: sqlite3.Row) -> SkillCandidate:
    return SkillCandidate(
        workflow_key=row["workflow_key"],
        title=row["title"],
        skill_name=row["skill_name"],
        confirmation_count=int(row["confirmation_count"]),
        status=row["status"],
        skill_markdown=row["skill_markdown"],
    )


def _validate_workflow_key(value: str) -> str:
    clean = value.strip().lower()
    if not _WORKFLOW_KEY.fullmatch(clean):
        raise ValueError("Workflow key должен быть kebab-case длиной до 63 символов.")
    return clean


def _required(value: str, label: str) -> str:
    clean = " ".join(value.split())
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    return clean


def _redact(value: str) -> str:
    return _SENSITIVE.sub("[REDACTED]", value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
