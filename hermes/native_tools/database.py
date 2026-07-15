"""One SQLite connection policy for JarHert Personal OS stores."""

from __future__ import annotations

import sqlite3
from pathlib import Path


DEFAULT_BUSY_TIMEOUT_MS = 10_000


def open_personal_os_database(
    database_path: str | Path,
    *,
    timeout_seconds: int = 10,
    autocommit: bool = False,
) -> sqlite3.Connection:
    """Open a Personal OS database with the shared concurrency settings."""
    path = Path(database_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=timeout_seconds,
        isolation_level=None if autocommit else "DEFERRED",
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)}")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
