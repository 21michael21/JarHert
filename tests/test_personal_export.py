from __future__ import annotations

import json
import sqlite3
import zipfile

from backend.db import init_db, make_session_factory
from backend.personal_export import PersonalExportService
from backend.personal_knowledge_store import SqlPersonalKnowledgeStore
from backend.stores import UserStore
from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from gateway_bot.service import GatewayService


def test_personal_export_is_user_scoped_and_restores_verified_archive(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'app.sqlite3'}")
    init_db(factory)
    users = UserStore(factory)
    first = users.get_or_create(1001)
    second = users.get_or_create(2002)
    notes = SqlPersonalKnowledgeStore(factory)
    notes.create(user_id=first.id, text="Моя заметка", project="Hub_ML")
    notes.create(user_id=second.id, text="Чужая заметка", project="Private")
    personal_os = tmp_path / "personal-os.sqlite3"
    with sqlite3.connect(personal_os) as connection:
        connection.execute("CREATE TABLE memory_blocks(id INTEGER PRIMARY KEY, content TEXT)")
        connection.execute("INSERT INTO memory_blocks(content) VALUES ('confirmed fact')")

    service = PersonalExportService(factory, personal_os_path=personal_os, export_dir=tmp_path / "exports")
    archive = service.create(user_id=first.id, tg_user_id=1001)
    restored = service.restore(archive, tmp_path / "restored")

    assert service.verify(archive)["ok"] is True
    with zipfile.ZipFile(archive) as bundle:
        account = json.loads(bundle.read("account.json"))
    serialized = json.dumps(account, ensure_ascii=False)
    assert "Моя заметка" in serialized
    assert "Чужая заметка" not in serialized
    assert restored["account"].exists()
    assert restored["personal_os"].exists()
    with sqlite3.connect(restored["personal_os"]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    gateway = GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        users=users,
        personal_exports=service,
    )
    assert gateway.create_personal_export(1001).is_file()
