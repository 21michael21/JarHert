from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.migrations import current_revision, run_migrations


CANARY_TG_USER_ID = 950_000_001


def run_backup_restore_check(workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "source.sqlite3"
    backup = workdir / "backup.sqlite3"
    restored = workdir / "restored.sqlite3"
    source_url = f"sqlite:///{source}"
    restored_url = f"sqlite:///{restored}"
    run_migrations(source_url)
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO users (tg_user_id) VALUES (?)", (CANARY_TG_USER_ID,))
        connection.commit()
        with sqlite3.connect(backup) as backup_connection:
            connection.backup(backup_connection)
    with sqlite3.connect(backup) as backup_connection:
        with sqlite3.connect(restored) as restored_connection:
            backup_connection.backup(restored_connection)
    with sqlite3.connect(restored) as connection:
        row = connection.execute(
            "SELECT tg_user_id FROM users WHERE tg_user_id = ?",
            (CANARY_TG_USER_ID,),
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    source_revision = current_revision(source_url)
    restored_revision = current_revision(restored_url)
    return {
        "ok": bool(row) and integrity == "ok" and source_revision == restored_revision,
        "source_revision": source_revision,
        "restored_revision": restored_revision,
        "canary_tg_user_id": row[0] if row else None,
        "integrity_check": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and restore a disposable SQLite backup canary.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jarhert-backup-restore-") as temp_dir:
        report = run_backup_restore_check(Path(temp_dir))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
