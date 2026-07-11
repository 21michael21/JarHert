from __future__ import annotations

import json
import ipaddress
import re
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .events import EventStore


ALLOWED_MONITOR_SOURCES = frozenset({"github_releases", "rss", "json_api", "allowed_url"})
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
        if len(raw) > 1_000_000:
            raise ValueError("GitHub release payload превышает лимит 1 MB.")
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


class RssSource:
    def __init__(self, *, fetch_bytes: FetchBytes | None = None) -> None:
        self._fetch_bytes = fetch_bytes or _fetch_bytes

    def fetch(self, config: dict[str, Any]) -> dict[str, Any]:
        url = _allowed_url(config)
        raw = _small_fetch(self._fetch_bytes, url, accept="application/rss+xml, application/xml, text/xml")
        root = ET.fromstring(raw)
        channel = root.find("channel")
        parent = channel if channel is not None else root
        items = []
        for item in parent.findall(".//item")[:20]:
            items.append(
                {
                    "title": _xml_text(item, "title"),
                    "link": _xml_text(item, "link"),
                    "published": _xml_text(item, "pubDate"),
                }
            )
        return {
            "url": url,
            "title": _xml_text(channel, "title") if channel is not None else "",
            "items": items,
        }


class AllowedJsonSource:
    def __init__(self, *, fetch_bytes: FetchBytes | None = None) -> None:
        self._fetch_bytes = fetch_bytes or _fetch_bytes

    def fetch(self, config: dict[str, Any]) -> dict[str, Any]:
        url = _allowed_url(config)
        payload = json.loads(_small_fetch(self._fetch_bytes, url, accept="application/json"))
        if not isinstance(payload, dict):
            raise ValueError("JSON monitor должен вернуть объект.")
        return {"url": url, "payload": payload}


class AllowedUrlSource:
    def __init__(self, *, fetch_bytes: FetchBytes | None = None) -> None:
        self._fetch_bytes = fetch_bytes or _fetch_bytes

    def fetch(self, config: dict[str, Any]) -> dict[str, Any]:
        url = _allowed_url(config)
        parser = _VisibleTextParser()
        parser.feed(_small_fetch(self._fetch_bytes, url, accept="text/html, text/plain").decode("utf-8"))
        return {"url": url, "text": " ".join(parser.parts)[:4000]}


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
        else:
            _allowed_url(source_config)
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

    def defer_change(self, monitor: Monitor, change: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO monitor_digest_items(
                    event_id, monitor_name, payload_json, status
                ) VALUES (?, ?, ?, 'pending')
                """,
                (int(change["event_id"]), monitor.name, _canonical_json(change)),
            )

    def build_digest(self, *, limit: int = 100) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM monitor_digest_items WHERE status = 'pending'
                ORDER BY id LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return {
            "items": [json.loads(row["payload_json"]) for row in rows],
            "item_ids": [int(row["id"]) for row in rows],
        }

    def mark_digest_delivered(self, item_ids: list[int]) -> None:
        ids = [int(item_id) for item_id in item_ids]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE monitor_digest_items SET status = 'delivered' WHERE id IN ({placeholders})",
                ids,
            )

    def count_emissions(self, day_key: str) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM monitor_emissions WHERE day_key = ?", (day_key,)
                ).fetchone()[0]
            )

    def record_emission(self, *, event_id: int, day_key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO monitor_emissions(event_id, day_key) VALUES (?, ?)",
                (int(event_id), day_key),
            )

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS monitor_emissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL UNIQUE,
                    day_key TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS monitor_digest_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL UNIQUE,
                    monitor_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
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
        self.sources = sources or {
            "github_releases": GitHubReleasesSource(),
            "rss": RssSource(),
            "json_api": AllowedJsonSource(),
            "allowed_url": AllowedUrlSource(),
        }

    def run_once(
        self,
        *,
        now: str | datetime | None = None,
        daily_emit_limit: int = 10,
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        day_key = _utc_now(now).date().isoformat()
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
                change = {
                    "monitor": monitor.name,
                    "source_type": monitor.source_type,
                    "condition": monitor.condition,
                    "diff": result.diff,
                    "current": payload,
                    "event_id": result.event_id,
                }
                if _quiet_hours_active(monitor.source_config, now=now):
                    self.registry.defer_change(monitor, change)
                elif self.registry.count_emissions(day_key) >= max(0, int(daily_emit_limit)):
                    self.registry.defer_change(monitor, change)
                else:
                    self.registry.record_emission(event_id=int(result.event_id), day_key=day_key)
                    changes.append(change)
        return changes


def _fetch_bytes(url: str, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(1_000_001)


def _small_fetch(fetcher: FetchBytes, url: str, *, accept: str) -> bytes:
    raw = fetcher(url, {"Accept": accept, "User-Agent": "JarHert-Hermes-Monitor/1.0"}, 10)
    if len(raw) > 512_000:
        raise ValueError("Monitor payload превышает лимит 500 KB.")
    return raw


def _allowed_url(config: dict[str, Any]) -> str:
    url = str(config.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Monitor URL должен быть HTTPS без credentials.")
    host = parsed.hostname.casefold()
    allowed = config.get("allowed_hosts") or []
    if isinstance(allowed, str):
        allowed_hosts = {part.strip().casefold() for part in allowed.split(",") if part.strip()}
    else:
        allowed_hosts = {str(part).strip().casefold() for part in allowed if str(part).strip()}
    if host not in allowed_hosts:
        raise ValueError("Monitor URL host отсутствует в allowlist.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Private network monitor URL запрещён.")
    return url


def _quiet_hours_active(config: dict[str, Any], *, now: str | datetime | None) -> bool:
    value = str(config.get("quiet_hours") or "").strip()
    if not value:
        return False
    try:
        start_raw, end_raw = value.split("-", 1)
        start = _minutes(start_raw)
        end = _minutes(end_raw)
        zone = ZoneInfo(str(config.get("timezone") or "Europe/Moscow"))
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("quiet_hours должен быть HH:MM-HH:MM с корректным timezone.") from error
    current = datetime.fromisoformat(now.replace("Z", "+00:00")) if isinstance(now, str) else now
    local = (current or datetime.now(zone)).astimezone(zone)
    minute = local.hour * 60 + local.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def _utc_now(value: str | datetime | None) -> datetime:
    current = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if current is None:
        return datetime.now(ZoneInfo("UTC"))
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Monitor time должен содержать timezone.")
    return current.astimezone(ZoneInfo("UTC"))


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("invalid time")
    return hour * 60 + minute


def _xml_text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    child = node.find(name)
    return (child.text or "").strip() if child is not None else ""


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean and not self._hidden_depth:
            self.parts.append(clean)


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
