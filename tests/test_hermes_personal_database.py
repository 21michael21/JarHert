from __future__ import annotations

import sqlite3
from pathlib import Path

from hermes.native_tools.database import open_personal_os_database
from hermes.native_tools.mcp_api import NativeToolsAPI


def test_personal_database_owns_sqlite_connection_defaults(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "personal.sqlite3"

    with open_personal_os_database(database) as connection:
        connection.execute("CREATE TABLE item (value TEXT)")
        connection.execute("INSERT INTO item(value) VALUES ('ok')")
        row = connection.execute("SELECT value FROM item").fetchone()

        assert isinstance(row, sqlite3.Row)
        assert row["value"] == "ok"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    assert database.is_file()


def test_personal_database_supports_explicit_autocommit_for_claiming_workers(tmp_path: Path) -> None:
    connection = open_personal_os_database(tmp_path / "personal.sqlite3", autocommit=True)
    try:
        assert connection.isolation_level is None
    finally:
        connection.close()


def test_native_stores_use_the_shared_personal_database_factory() -> None:
    root = Path(__file__).resolve().parents[1]
    modules = (
        "action_plans.py",
        "capabilities.py",
        "coding_jobs.py",
        "contacts.py",
        "events.py",
        "knowledge_archive.py",
        "memory_consolidation.py",
        "monitors.py",
        "personal_crm.py",
        "personal_os.py",
        "personal_productivity.py",
        "personal_rhythms.py",
        "shopping.py",
        "skill_distillation.py",
        "subscriptions.py",
        "trips.py",
    )

    for name in modules:
        source = (root / "hermes" / "native_tools" / name).read_text(encoding="utf-8")
        assert "open_personal_os_database" in source, name
        assert "sqlite3.connect(self.database_path" not in source, name


def test_native_api_reuses_personal_os_stores_within_one_runtime(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    assert api._personal_os() is api._personal_os()
    assert api._productivity() is api._productivity()
    assert api._contacts() is api._contacts()
