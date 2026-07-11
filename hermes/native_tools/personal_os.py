from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MEMORY_BLOCK_TYPES = frozenset({"profile", "person", "project", "commitment", "preference"})
PROJECT_TOOL_ALLOWLIST = frozenset(
    {"tasks", "calendar", "notes", "reminders", "contacts", "messages", "monitors", "sandbox"}
)


@dataclass(frozen=True)
class MemoryBlock:
    id: int
    block_type: str
    subject: str
    content: str
    project: str | None
    updated_at: str


@dataclass(frozen=True)
class ProjectContext:
    id: int
    key: str
    name: str
    aliases: tuple[str, ...]
    trello_board: str | None
    trello_list: str | None
    calendar_id: str | None
    contacts: tuple[str, ...]
    tools: tuple[str, ...]
    context_note: str | None
    enabled: bool
    updated_at: str


class PersonalOSStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert_memory_block(
        self,
        *,
        block_type: str,
        subject: str,
        content: str,
        project: str | None = None,
    ) -> MemoryBlock:
        kind = _allowed(block_type, MEMORY_BLOCK_TYPES, "Memory block type")
        clean_subject = _required(subject, "Memory subject", limit=160)
        clean_content = _required(content, "Memory content", limit=4000)
        clean_project = _optional(project, limit=120)
        project_key = clean_project or ""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_blocks(block_type, subject, content, project_key, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(block_type, subject, project_key) DO UPDATE SET
                    content = excluded.content,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (kind, clean_subject, clean_content, project_key),
            )
            row = connection.execute(
                """
                SELECT * FROM memory_blocks
                WHERE block_type = ? AND subject = ? AND project_key = ?
                """,
                (kind, clean_subject, project_key),
            ).fetchone()
        return _memory_from_row(row)

    def list_memory_blocks(
        self,
        *,
        block_type: str | None = None,
        project: str | None = None,
        limit: int = 50,
    ) -> list[MemoryBlock]:
        clauses: list[str] = []
        values: list[Any] = []
        if block_type:
            clauses.append("block_type = ?")
            values.append(_allowed(block_type, MEMORY_BLOCK_TYPES, "Memory block type"))
        if project:
            clauses.append("project_key = ?")
            values.append(_required(project, "Project", limit=120))
        query = "SELECT * FROM memory_blocks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        values.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_memory_from_row(row) for row in rows]

    def upsert_project(
        self,
        *,
        key: str,
        name: str,
        aliases: list[str] | None = None,
        trello_board: str | None = None,
        trello_list: str | None = None,
        calendar_id: str | None = None,
        contacts: list[str] | None = None,
        tools: list[str] | None = None,
        context_note: str | None = None,
    ) -> ProjectContext:
        clean_key = _required(key, "Project key", limit=80)
        clean_name = _required(name, "Project name", limit=160)
        clean_tools = _unique(tools or [])
        unknown = sorted(set(clean_tools) - PROJECT_TOOL_ALLOWLIST)
        if unknown:
            raise ValueError(f"Project tools отсутствуют в allowlist: {', '.join(unknown)}")
        clean_aliases = _unique([clean_key, clean_name, *(aliases or [])])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_contexts(
                    project_key, name, aliases_json, trello_board, trello_list,
                    calendar_id, contacts_json, tools_json, context_note, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(project_key) DO UPDATE SET
                    name = excluded.name,
                    aliases_json = excluded.aliases_json,
                    trello_board = excluded.trello_board,
                    trello_list = excluded.trello_list,
                    calendar_id = excluded.calendar_id,
                    contacts_json = excluded.contacts_json,
                    tools_json = excluded.tools_json,
                    context_note = excluded.context_note,
                    enabled = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    clean_key,
                    clean_name,
                    _json(clean_aliases),
                    _optional(trello_board, limit=160),
                    _optional(trello_list, limit=160),
                    _optional(calendar_id, limit=240),
                    _json(_unique(contacts or [])),
                    _json(clean_tools),
                    _optional(context_note, limit=2000),
                ),
            )
            row = connection.execute(
                "SELECT * FROM project_contexts WHERE project_key = ?",
                (clean_key,),
            ).fetchone()
        return _project_from_row(row)

    def list_projects(self, *, enabled_only: bool = True) -> list[ProjectContext]:
        query = "SELECT * FROM project_contexts"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name COLLATE NOCASE, id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_project_from_row(row) for row in rows]

    def resolve_project(self, text: str) -> ProjectContext | None:
        haystack = _normalize(_required(text, "Text", limit=4000))
        matches: list[tuple[int, ProjectContext]] = []
        for project in self.list_projects():
            lengths = [len(alias) for alias in project.aliases if _normalize(alias) in haystack]
            if lengths:
                matches.append((max(lengths), project))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            raise ValueError("Фраза совпала с несколькими проектами одинаково точно.")
        return matches[0][1]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    project_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(block_type, subject, project_key)
                );
                CREATE INDEX IF NOT EXISTS ix_memory_blocks_type_project
                    ON memory_blocks(block_type, project_key, updated_at);
                CREATE TABLE IF NOT EXISTS project_contexts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    trello_board TEXT,
                    trello_list TEXT,
                    calendar_id TEXT,
                    contacts_json TEXT NOT NULL DEFAULT '[]',
                    tools_json TEXT NOT NULL DEFAULT '[]',
                    context_note TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _memory_from_row(row: sqlite3.Row) -> MemoryBlock:
    return MemoryBlock(
        id=int(row["id"]),
        block_type=str(row["block_type"]),
        subject=str(row["subject"]),
        content=str(row["content"]),
        project=str(row["project_key"]) or None,
        updated_at=str(row["updated_at"]),
    )


def _project_from_row(row: sqlite3.Row) -> ProjectContext:
    return ProjectContext(
        id=int(row["id"]),
        key=str(row["project_key"]),
        name=str(row["name"]),
        aliases=tuple(json.loads(row["aliases_json"])),
        trello_board=str(row["trello_board"]) if row["trello_board"] else None,
        trello_list=str(row["trello_list"]) if row["trello_list"] else None,
        calendar_id=str(row["calendar_id"]) if row["calendar_id"] else None,
        contacts=tuple(json.loads(row["contacts_json"])),
        tools=tuple(json.loads(row["tools_json"])),
        context_note=str(row["context_note"]) if row["context_note"] else None,
        enabled=bool(row["enabled"]),
        updated_at=str(row["updated_at"]),
    )


def _allowed(value: str, allowed: frozenset[str], label: str) -> str:
    clean = _required(value, label, limit=80).casefold()
    if clean not in allowed:
        raise ValueError(f"{label} отсутствует в allowlist.")
    return clean


def _required(value: str, label: str, *, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean


def _optional(value: str | None, *, limit: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _required(value, "Value", limit=limit)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _required(value, "List item", limit=160)
        key = _normalize(clean)
        if key not in seen:
            result.append(clean)
            seen.add(key)
    return result


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
