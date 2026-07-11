from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urlunparse


FetchBytes = Callable[[str, dict[str, str], float], bytes]
_MAX_PAGE_BYTES = 1_000_000
_MAX_TEXT_CHARS = 200_000
_MAX_SNAPSHOTS_PER_SOURCE = 20
_SEARCH_TOKEN = re.compile(r"[\w-]{2,}", re.UNICODE)


@dataclass(frozen=True)
class KnowledgeSource:
    id: int
    url: str
    title: str
    project: str | None
    snapshot_count: int
    updated_at: str


class KnowledgeArchive:
    """Archive explicitly requested public pages and keep their latest text searchable."""

    def __init__(self, database_path: str | Path, *, fetcher: FetchBytes | None = None) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or _fetch_web_bytes
        self._initialize()

    def archive_url(self, url: str, *, project: str | None = None) -> dict[str, object]:
        clean_url = validate_archive_url(url)
        raw = self.fetcher(
            clean_url,
            {"Accept": "text/html, text/plain;q=0.9", "User-Agent": "JarHert-Knowledge-Archive/1.0"},
            12,
        )
        if len(raw) > _MAX_PAGE_BYTES:
            raise ValueError("Страница превышает лимит 1 MB.")
        title, content = _extract_page(raw)
        if not content:
            raise ValueError("На странице нет читаемого текста.")
        content_hash = hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()
        clean_project = _optional(project, limit=120)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute("SELECT * FROM knowledge_sources WHERE url = ?", (clean_url,)).fetchone()
            if source is None:
                source_id = int(
                    connection.execute(
                        "INSERT INTO knowledge_sources(url, title, project_key) VALUES (?, ?, ?)",
                        (clean_url, title, clean_project),
                    ).lastrowid
                )
            else:
                source_id = int(source["id"])
                connection.execute(
                    """
                    UPDATE knowledge_sources
                    SET title = ?, project_key = COALESCE(?, project_key), updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (title, clean_project, source_id),
                )
            latest = connection.execute(
                "SELECT content_hash FROM knowledge_snapshots WHERE source_id = ? ORDER BY id DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            changed = latest is None or str(latest["content_hash"]) != content_hash
            if changed:
                connection.execute(
                    """
                    INSERT INTO knowledge_snapshots(source_id, content_hash, title, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (source_id, content_hash, title, content),
                )
                connection.execute("DELETE FROM knowledge_fts WHERE source_id = ?", (source_id,))
                connection.execute(
                    "INSERT INTO knowledge_fts(source_id, title, content) VALUES (?, ?, ?)",
                    (source_id, title, content),
                )
                self._trim_snapshots(connection, source_id)
            row = self._source_row(connection, source_id)
        payload = _source_payload(_source_from_row(row))
        payload["changed"] = changed
        return payload

    def search(self, query: str, *, project: str | None = None, limit: int = 10) -> list[dict[str, object]]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        values: list[object] = [fts_query]
        clauses = ["knowledge_fts MATCH ?"]
        if project:
            clauses.append("sources.project_key = ?")
            values.append(_required(project, "Project", limit=120))
        values.append(max(1, min(int(limit), 50)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT knowledge_fts.source_id, knowledge_fts.title, knowledge_fts.content,
                       sources.url, sources.project_key, sources.updated_at
                FROM knowledge_fts
                JOIN knowledge_sources AS sources ON sources.id = knowledge_fts.source_id
                WHERE {' AND '.join(clauses)}
                ORDER BY rank
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [
            {
                "source_id": int(row["source_id"]),
                "source_url": str(row["url"]),
                "title": str(row["title"]),
                "project": str(row["project_key"]) or None,
                "updated_at": str(row["updated_at"]),
                "excerpt": _excerpt(str(row["content"]), query),
            }
            for row in rows
        ]

    def list_sources(self, *, project: str | None = None, limit: int = 100) -> list[KnowledgeSource]:
        values: list[object] = []
        clause = ""
        if project:
            clause = "WHERE project_key = ?"
            values.append(_required(project, "Project", limit=120))
        values.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT sources.*, COUNT(snapshots.id) AS snapshot_count
                FROM knowledge_sources AS sources
                LEFT JOIN knowledge_snapshots AS snapshots ON snapshots.source_id = sources.id
                {clause}
                GROUP BY sources.id
                ORDER BY sources.updated_at DESC, sources.id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [_source_from_row(row) for row in rows]

    def _trim_snapshots(self, connection: sqlite3.Connection, source_id: int) -> None:
        rows = connection.execute(
            "SELECT id FROM knowledge_snapshots WHERE source_id = ? ORDER BY id DESC",
            (source_id,),
        ).fetchall()
        stale_ids = [int(row["id"]) for row in rows[_MAX_SNAPSHOTS_PER_SOURCE:]]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            connection.execute(f"DELETE FROM knowledge_snapshots WHERE id IN ({placeholders})", stale_ids)

    def _source_row(self, connection: sqlite3.Connection, source_id: int) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT sources.*, COUNT(snapshots.id) AS snapshot_count
            FROM knowledge_sources AS sources
            LEFT JOIN knowledge_snapshots AS snapshots ON snapshots.source_id = sources.id
            WHERE sources.id = ?
            GROUP BY sources.id
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Knowledge source disappeared during archive.")
        return row

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    project_key TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS knowledge_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
                    content_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS ix_knowledge_snapshots_source ON knowledge_snapshots(source_id, id DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    source_id UNINDEXED,
                    title,
                    content,
                    tokenize='unicode61'
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def validate_archive_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Архивировать можно только публичный HTTPS URL без credentials.")
    if parsed.port not in {None, 443}:
        raise ValueError("Архивировать можно только стандартный HTTPS-порт.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Внутренний IP-адрес архивировать нельзя.")
    return urlunparse(("https", parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def _fetch_web_bytes(url: str, headers: dict[str, str], timeout: float) -> bytes:
    host = urlparse(url).hostname
    if not host:
        raise ValueError("URL не содержит host.")
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("URL ведёт во внутреннюю сеть.")
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if not 200 <= int(response.status) < 300:
                raise ValueError(f"Страница вернула HTTP {response.status}.")
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            if not content_type.startswith(("text/html", "text/plain")):
                raise ValueError("Архив поддерживает только HTML или текстовые страницы.")
            return response.read(_MAX_PAGE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            raise ValueError("Редирект не разрешён: укажи финальный HTTPS URL.") from error
        raise ValueError(f"Страница вернула HTTP {error.code}.") from error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self._hidden_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, _attrs) -> None:  # type: ignore[no-untyped-def]
        name = tag.casefold()
        if name in {"script", "style", "noscript", "template", "svg"}:
            self._hidden_depth += 1
        elif name == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"script", "style", "noscript", "template", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1
        elif name == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean or self._hidden_depth:
            return
        if self._in_title:
            self.title_parts.append(clean)
        self.parts.append(clean)


def _extract_page(raw: bytes) -> tuple[str, str]:
    parser = _PageTextParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    parser.close()
    title = " ".join(parser.title_parts).strip()[:240] or "Без названия"
    content = " ".join(parser.parts).strip()[:_MAX_TEXT_CHARS]
    return title, content


def _fts_query(query: str) -> str:
    tokens = _SEARCH_TOKEN.findall(str(query or ""))[:10]
    return " AND ".join(f'"{token}"' for token in tokens)


def _excerpt(content: str, query: str) -> str:
    tokens = _SEARCH_TOKEN.findall(str(query or ""))
    lowered = content.casefold()
    position = min((lowered.find(token.casefold()) for token in tokens if token.casefold() in lowered), default=0)
    start = max(0, position - 100)
    end = min(len(content), start + 360)
    prefix = "…" if start else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def _source_from_row(row: sqlite3.Row) -> KnowledgeSource:
    return KnowledgeSource(
        id=int(row["id"]),
        url=str(row["url"]),
        title=str(row["title"]),
        project=str(row["project_key"]) if row["project_key"] else None,
        snapshot_count=int(row["snapshot_count"]),
        updated_at=str(row["updated_at"]),
    )


def _source_payload(source: KnowledgeSource) -> dict[str, object]:
    return {
        "source_id": source.id,
        "url": source.url,
        "title": source.title,
        "project": source.project,
        "snapshot_count": source.snapshot_count,
        "updated_at": source.updated_at,
    }


def _required(value: str, label: str, *, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean


def _optional(value: str | None, *, limit: int) -> str | None:
    return _required(value, "Value", limit=limit) if value is not None and str(value).strip() else None
