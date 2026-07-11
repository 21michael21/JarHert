from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIRMED_BLOCK_TYPES = frozenset({"profile", "person", "project", "commitment", "preference"})


@dataclass(frozen=True)
class MemorySnapshot:
    scope: str
    facts: tuple[dict[str, Any], ...]
    state_hash: str
    updated_at: str


class MemoryConsolidator:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def consolidate(self) -> dict[str, int | str]:
        grouped = self._collect_confirmed_facts()
        changed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_scopes = {
                str(row["scope"])
                for row in connection.execute("SELECT scope FROM memory_consolidations").fetchall()
            }
            for scope, facts in grouped.items():
                payload = _canonical_json(facts)
                state_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                previous = connection.execute(
                    "SELECT state_hash FROM memory_consolidations WHERE scope = ?", (scope,)
                ).fetchone()
                if previous is not None and previous["state_hash"] == state_hash:
                    continue
                connection.execute(
                    """
                    INSERT INTO memory_consolidations(scope, facts_json, state_hash, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(scope) DO UPDATE SET
                        facts_json = excluded.facts_json,
                        state_hash = excluded.state_hash,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (scope, payload, state_hash),
                )
                changed += 1
            stale = existing_scopes - set(grouped)
            if stale:
                placeholders = ",".join("?" for _ in stale)
                connection.execute(
                    f"DELETE FROM memory_consolidations WHERE scope IN ({placeholders})", tuple(stale)
                )
                changed += len(stale)
            connection.commit()
        return {
            "status": "updated" if changed else "no_change",
            "scopes": len(grouped),
            "facts": sum(len(items) for items in grouped.values()),
        }

    def list_snapshots(self) -> list[MemorySnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_consolidations
                ORDER BY CASE WHEN scope = 'global' THEN 0 ELSE 1 END, scope COLLATE NOCASE
                """
            ).fetchall()
        return [
            MemorySnapshot(
                scope=str(row["scope"]),
                facts=tuple(json.loads(row["facts_json"])),
                state_hash=str(row["state_hash"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def _collect_confirmed_facts(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        with self._connect() as connection:
            if _table_exists(connection, "memory_blocks"):
                for row in connection.execute("SELECT * FROM memory_blocks ORDER BY id").fetchall():
                    kind = str(row["block_type"])
                    if kind not in CONFIRMED_BLOCK_TYPES:
                        continue
                    _append_fact(
                        grouped,
                        _scope(row["project_key"]),
                        {"kind": kind, "subject": str(row["subject"]), "text": str(row["content"])},
                    )
            if _table_exists(connection, "commitments"):
                for row in connection.execute(
                    "SELECT * FROM commitments WHERE status = 'open' ORDER BY id"
                ).fetchall():
                    _append_fact(
                        grouped,
                        _scope(row["project_key"]),
                        {
                            "kind": "commitment",
                            "subject": str(row["subject"]),
                            "text": str(row["content"]),
                            "contact": str(row["contact"]) if row["contact"] else None,
                            "due_at": str(row["due_at"]) if row["due_at"] else None,
                        },
                    )
            if _table_exists(connection, "crm_interactions"):
                for row in connection.execute(
                    "SELECT * FROM crm_interactions WHERE kind = 'agreement' ORDER BY id"
                ).fetchall():
                    _append_fact(
                        grouped,
                        _scope(row["project_key"]),
                        {
                            "kind": "agreement",
                            "subject": str(row["contact"]),
                            "text": str(row["summary"]),
                            "next_contact_at": str(row["next_contact_at"]) if row["next_contact_at"] else None,
                        },
                    )
        return grouped

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_consolidations (
                    scope TEXT PRIMARY KEY,
                    facts_json TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _append_fact(grouped: dict[str, list[dict[str, Any]]], scope: str, fact: dict[str, Any]) -> None:
    clean = {key: value for key, value in fact.items() if value not in {None, ""}}
    key = _canonical_json(clean).casefold()
    existing = {_canonical_json(item).casefold() for item in grouped.setdefault(scope, [])}
    if key not in existing:
        grouped[scope].append(clean)


def _scope(value: Any) -> str:
    return str(value or "").strip() or "global"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
