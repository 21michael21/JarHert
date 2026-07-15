from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import open_personal_os_database


ALLOWED_EVENT_ACTIONS = frozenset({"evaluate", "notify"})


@dataclass(frozen=True)
class MonitorCheck:
    status: str
    changed: bool
    diff: dict[str, list[dict[str, Any]]]
    event_id: int | None = None


@dataclass(frozen=True)
class Event:
    id: int
    event_type: str
    source: str
    payload: dict[str, Any]
    status: str


@dataclass(frozen=True)
class EventAction:
    id: int
    event_id: int
    rule_id: int
    action_type: str
    payload: dict[str, Any]
    status: str
    idempotency_key: str


class EventStore:
    """Deterministic event graph storage for the native Hermes profile."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def check_monitor(self, *, name: str, source_type: str, payload: dict[str, Any]) -> MonitorCheck:
        monitor_name = _required(name, "Monitor name")
        source = _required(source_type, "Source type")
        if not isinstance(payload, dict):
            raise ValueError("Monitor payload должен быть JSON-объектом.")
        canonical = _canonical_json(payload)
        state_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        checked_at = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT last_hash, last_payload_json FROM monitor_states WHERE name = ?",
                (monitor_name,),
            ).fetchone()
            if previous is None:
                connection.execute(
                    """
                    INSERT INTO monitor_states(name, source_type, last_hash, last_payload_json, checked_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (monitor_name, source, state_hash, canonical, checked_at),
                )
                self._record_monitor_run(connection, monitor_name, "baseline", False, _empty_diff(), None)
                connection.commit()
                return MonitorCheck(status="baseline", changed=False, diff=_empty_diff())

            if previous["last_hash"] == state_hash:
                connection.execute(
                    "UPDATE monitor_states SET checked_at = ? WHERE name = ?",
                    (checked_at, monitor_name),
                )
                self._record_monitor_run(connection, monitor_name, "no_change", False, _empty_diff(), None)
                connection.commit()
                return MonitorCheck(status="no_change", changed=False, diff=_empty_diff())

            old_payload = json.loads(previous["last_payload_json"])
            diff = compact_json_diff(old_payload, payload)
            event_payload = {
                "monitor": monitor_name,
                "source_type": source,
                "diff": diff,
                "current": payload,
            }
            event_id = int(
                connection.execute(
                    """
                    INSERT INTO events(event_type, source, payload_json, fingerprint, status)
                    VALUES ('monitor.changed', ?, ?, ?, 'pending')
                    ON CONFLICT(fingerprint) DO UPDATE SET fingerprint = excluded.fingerprint
                    RETURNING id
                    """,
                    (
                        monitor_name,
                        _canonical_json(event_payload),
                        f"monitor:{monitor_name}:{state_hash}",
                    ),
                ).fetchone()[0]
            )
            connection.execute(
                """
                UPDATE monitor_states
                SET source_type = ?, last_hash = ?, last_payload_json = ?, checked_at = ?
                WHERE name = ?
                """,
                (source, state_hash, canonical, checked_at, monitor_name),
            )
            self._record_monitor_run(connection, monitor_name, "changed", True, diff, event_id)
            connection.commit()
        return MonitorCheck(status="changed", changed=True, diff=diff, event_id=event_id)

    def add_rule(
        self,
        *,
        name: str,
        event_type: str,
        action_type: str,
        action_config: dict[str, Any],
    ) -> int:
        if action_type not in ALLOWED_EVENT_ACTIONS:
            raise ValueError(f"Action type '{action_type}' отсутствует в event allowlist.")
        if not isinstance(action_config, dict):
            raise ValueError("Action config должен быть JSON-объектом.")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO event_rules(name, event_type, action_type, action_config_json, enabled)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    _required(name, "Rule name"),
                    _required(event_type, "Event type"),
                    action_type,
                    _canonical_json(action_config),
                ),
            )
            return int(cursor.lastrowid)

    def dispatch_pending_events(self) -> dict[str, int]:
        event_count = 0
        action_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            events = connection.execute(
                "SELECT * FROM events WHERE status = 'pending' ORDER BY id"
            ).fetchall()
            for event in events:
                rules = connection.execute(
                    "SELECT * FROM event_rules WHERE enabled = 1 AND event_type = ? ORDER BY id",
                    (event["event_type"],),
                ).fetchall()
                event_payload = json.loads(event["payload_json"])
                for rule in rules:
                    key = f"event:{event['id']}:rule:{rule['id']}"
                    payload = {
                        "event": event_payload,
                        "config": json.loads(rule["action_config_json"]),
                    }
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO event_actions(
                            event_id, rule_id, action_type, payload_json, status, idempotency_key
                        ) VALUES (?, ?, ?, ?, 'queued', ?)
                        """,
                        (event["id"], rule["id"], rule["action_type"], _canonical_json(payload), key),
                    )
                    action_count += max(0, cursor.rowcount)
                connection.execute(
                    "UPDATE events SET status = 'dispatched', dispatched_at = ? WHERE id = ?",
                    (_now(), event["id"]),
                )
                event_count += 1
            connection.commit()
        return {"events": event_count, "actions": action_count}

    def list_events(self) -> list[Event]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY id").fetchall()
        return [
            Event(
                id=int(row["id"]),
                event_type=row["event_type"],
                source=row["source"],
                payload=json.loads(row["payload_json"]),
                status=row["status"],
            )
            for row in rows
        ]

    def list_actions(self) -> list[EventAction]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM event_actions ORDER BY id").fetchall()
        return [
            EventAction(
                id=int(row["id"]),
                event_id=int(row["event_id"]),
                rule_id=int(row["rule_id"]),
                action_type=row["action_type"],
                payload=json.loads(row["payload_json"]),
                status=row["status"],
                idempotency_key=row["idempotency_key"],
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitor_states (
                    name TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    last_hash TEXT NOT NULL,
                    last_payload_json TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    dispatched_at TEXT
                );
                CREATE TABLE IF NOT EXISTS monitor_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitor_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    changed INTEGER NOT NULL,
                    diff_json TEXT NOT NULL,
                    event_id INTEGER REFERENCES events(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS event_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_config_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS event_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL REFERENCES events(id),
                    rule_id INTEGER NOT NULL REFERENCES event_rules(id),
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _record_monitor_run(
        self,
        connection: sqlite3.Connection,
        monitor_name: str,
        status: str,
        changed: bool,
        diff: dict[str, list[dict[str, Any]]],
        event_id: int | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO monitor_runs(monitor_name, status, changed, diff_json, event_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (monitor_name, status, int(changed), _canonical_json(diff), event_id),
        )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path, timeout_seconds=5)


def compact_json_diff(before: Any, after: Any, path: str = "") -> dict[str, list[dict[str, Any]]]:
    result = _empty_diff()
    _walk_diff(before, after, path, result)
    return result


def _walk_diff(before: Any, after: Any, path: str, result: dict[str, list[dict[str, Any]]]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() - after.keys()):
            result["removed"].append({"path": _join_path(path, str(key)), "value": before[key]})
        for key in sorted(after.keys() - before.keys()):
            result["added"].append({"path": _join_path(path, str(key)), "value": after[key]})
        for key in sorted(before.keys() & after.keys()):
            _walk_diff(before[key], after[key], _join_path(path, str(key)), result)
        return
    if before != after:
        result["changed"].append({"path": path or "$", "before": before, "after": after})


def _join_path(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def _empty_diff() -> dict[str, list[dict[str, Any]]]:
    return {"added": [], "removed": [], "changed": []}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required(value: str, label: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    return clean


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
