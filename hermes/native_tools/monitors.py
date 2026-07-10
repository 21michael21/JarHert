from __future__ import annotations

import json
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .events import EventStore


ALLOWED_MONITOR_SOURCES = frozenset({"github_releases"})
_GITHUB_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
FetchBytes = Callable[[str, dict[str, str], float], bytes]


@dataclass(frozen=True)
class Monitor:
    id: int
    name: str
    source_type: str
    source_config: dict[str, Any]
    condition: str
    enabled: bool


class MonitorSource(Protocol):
    def fetch(self, config: dict[str, Any]) -> dict[str, Any]: ...


class GitHubReleasesSource:
    def __init__(self, *, fetch_bytes: FetchBytes | None = None) -> None:
        self._fetch_bytes = fetch_bytes or _fetch_bytes

    def fetch(self, config: dict[str, Any]) -> dict[str, Any]:
        owner = _github_name(config.get("owner"), "owner")
        repo = _github_name(config.get("repo"), "repo")
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        raw = self._fetch_bytes(
            url,
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "JarHert-Hermes-Monitor/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            10,
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not payload.get("tag_name"):
            raise ValueError("GitHub release payload не содержит tag_name.")
        assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
        return {
            "tag": str(payload["tag_name"]),
            "name": str(payload.get("name") or payload["tag_name"]),
            "url": str(payload.get("html_url") or ""),
            "published_at": str(payload.get("published_at") or ""),
            "notes": str(payload.get("body") or "")[:4000],
            "draft": bool(payload.get("draft")),
            "prerelease": bool(payload.get("prerelease")),
            "assets": [str(item.get("name")) for item in assets[:50] if isinstance(item, dict) and item.get("name")],
        }


class MonitorRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(
        self,
        *,
        name: str,
        source_type: str,
        source_config: dict[str, Any],
        condition: str,
    ) -> Monitor:
        if source_type not in ALLOWED_MONITOR_SOURCES:
            raise ValueError(f"Source type '{source_type}' отсутствует в monitor allowlist.")
        if not isinstance(source_config, dict):
            raise ValueError("Source config должен быть JSON-объектом.")
        clean_name = _required(name, "Monitor name")
        clean_condition = _required(condition, "Condition")
        if source_type == "github_releases":
            _github_name(source_config.get("owner"), "owner")
            _github_name(source_config.get("repo"), "repo")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO native_monitors(name, source_type, source_config_json, condition_text, enabled)
                VALUES (?, ?, ?, ?, 1)
                """,
                (clean_name, source_type, _canonical_json(source_config), clean_condition),
            )
            monitor_id = int(cursor.lastrowid)
        return self.get(monitor_id)

    def get(self, monitor_id: int) -> Monitor:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM native_monitors WHERE id = ?", (monitor_id,)).fetchone()
        if row is None:
            raise ValueError("Monitor не найден.")
        return _monitor_from_row(row)

    def list(self, *, enabled_only: bool = False) -> list[Monitor]:
        query = "SELECT * FROM native_monitors"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_monitor_from_row(row) for row in rows]

    def disable(self, monitor_id: int) -> Monitor:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE native_monitors SET enabled = 0 WHERE id = ?",
                (monitor_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Monitor не найден.")
        return self.get(monitor_id)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS native_monitors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    source_config_json TEXT NOT NULL,
                    condition_text TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


class MonitorRunner:
    def __init__(
        self,
        registry: MonitorRegistry,
        event_store: EventStore,
        *,
        sources: dict[str, MonitorSource] | None = None,
    ) -> None:
        self.registry = registry
        self.event_store = event_store
        self.sources = sources or {"github_releases": GitHubReleasesSource()}

    def run_once(self) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for monitor in self.registry.list(enabled_only=True):
            source = self.sources.get(monitor.source_type)
            if source is None:
                continue
            payload = source.fetch(monitor.source_config)
            result = self.event_store.check_monitor(
                name=monitor.name,
                source_type=monitor.source_type,
                payload=payload,
            )
            if result.changed:
                changes.append(
                    {
                        "monitor": monitor.name,
                        "source_type": monitor.source_type,
                        "condition": monitor.condition,
                        "diff": result.diff,
                        "current": payload,
                        "event_id": result.event_id,
                    }
                )
        return changes


def _fetch_bytes(url: str, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(1_000_001)


def _monitor_from_row(row: sqlite3.Row) -> Monitor:
    return Monitor(
        id=int(row["id"]),
        name=row["name"],
        source_type=row["source_type"],
        source_config=json.loads(row["source_config_json"]),
        condition=row["condition_text"],
        enabled=bool(row["enabled"]),
    )


def _github_name(value: Any, label: str) -> str:
    clean = str(value or "").strip()
    if not _GITHUB_NAME.fullmatch(clean):
        raise ValueError(f"GitHub {label} содержит недопустимые символы.")
    return clean


def _required(value: str, label: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    return clean


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
