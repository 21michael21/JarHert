from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db import Base
import backend.models  # noqa: F401 - register every exportable table in metadata.


EXPORT_TABLES = (
    "memories", "ideas", "notes", "note_history", "reminders",
    "conversation_turns", "user_preferences", "contacts", "contact_aliases",
    "agent_jobs", "agent_actions", "monitor_jobs", "training_examples", "coding_jobs",
)


class PersonalExportService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        personal_os_path: str | Path,
        export_dir: str | Path,
    ) -> None:
        self.session_factory = session_factory
        self.personal_os_path = Path(personal_os_path).expanduser()
        self.export_dir = Path(export_dir).expanduser()

    def create(self, *, user_id: int, tg_user_id: int) -> Path:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        account = {
            "format": "jarhert-personal-export-v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tg_user_id": tg_user_id,
            "tables": self._account_rows(user_id),
        }
        files: dict[str, bytes] = {
            "account.json": json.dumps(account, ensure_ascii=False, indent=2).encode("utf-8")
        }
        personal_os = self._personal_os_snapshot()
        if personal_os is not None:
            files["personal-os.sqlite3"] = personal_os
        manifest = {
            "format": account["format"],
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        }
        files["manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
        archive = self.export_dir / f"jarhert-export-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in files.items():
                bundle.writestr(name, content)
        os.chmod(archive, 0o600)
        if not self.verify(archive)["ok"]:
            archive.unlink(missing_ok=True)
            raise RuntimeError("Personal export verification failed")
        return archive

    def verify(self, archive: str | Path) -> dict[str, object]:
        allowed = {"account.json", "manifest.json", "personal-os.sqlite3"}
        try:
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                if not {"account.json", "manifest.json"} <= names or not names <= allowed:
                    return {"ok": False, "error": "unexpected archive members"}
                manifest = json.loads(bundle.read("manifest.json"))
                account = json.loads(bundle.read("account.json"))
                if account.get("format") != "jarhert-personal-export-v1":
                    return {"ok": False, "error": "unsupported format"}
                for name, expected in manifest.get("files", {}).items():
                    if name not in names or hashlib.sha256(bundle.read(name)).hexdigest() != expected:
                        return {"ok": False, "error": f"checksum mismatch: {name}"}
                if "personal-os.sqlite3" in names and not _sqlite_bytes_ok(bundle.read("personal-os.sqlite3")):
                    return {"ok": False, "error": "personal os integrity failed"}
        except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            return {"ok": False, "error": type(error).__name__}
        return {"ok": True, "files": sorted(names)}

    def restore(self, archive: str | Path, destination: str | Path) -> dict[str, Path | None]:
        result = self.verify(archive)
        if not result["ok"]:
            raise ValueError(f"Invalid personal export: {result.get('error')}")
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            account_path = target / "account.json"
            account_path.write_bytes(bundle.read("account.json"))
            personal_path = None
            if "personal-os.sqlite3" in bundle.namelist():
                personal_path = target / "personal-os.sqlite3"
                personal_path.write_bytes(bundle.read("personal-os.sqlite3"))
                os.chmod(personal_path, 0o600)
        os.chmod(account_path, 0o600)
        return {"account": account_path, "personal_os": personal_path}

    def _account_rows(self, user_id: int) -> dict[str, list[dict[str, object]]]:
        result: dict[str, list[dict[str, object]]] = {}
        with self.session_factory() as db:
            for name in EXPORT_TABLES:
                table = Base.metadata.tables.get(name)
                if table is None or "user_id" not in table.c:
                    continue
                rows = db.execute(select(table).where(table.c.user_id == user_id)).mappings().all()
                result[name] = [{key: _json_value(value) for key, value in row.items()} for row in rows]
        return result

    def _personal_os_snapshot(self) -> bytes | None:
        if not self.personal_os_path.is_file():
            return None
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as temporary:
            with sqlite3.connect(self.personal_os_path) as source, sqlite3.connect(temporary.name) as target:
                source.backup(target)
            return Path(temporary.name).read_bytes()


def _sqlite_bytes_ok(content: bytes) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as temporary:
        temporary.write(content)
        temporary.flush()
        try:
            with sqlite3.connect(temporary.name) as connection:
                return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError:
            return False


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
