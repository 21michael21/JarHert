from __future__ import annotations

from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


def test_consolidation_uses_only_confirmed_facts_and_is_noop_when_unchanged(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    api.memory_block_upsert(
        block_type="preference",
        subject="response-style",
        content="Пиши коротко и живо",
    )
    api.memory_block_upsert(
        block_type="note",
        subject="сырой inbox",
        content="Эта неподтверждённая заметка не должна попасть в summary",
    )
    api.commitment_create(
        subject="Ответить Илье",
        content="Отправить ревью",
        project="Hub_ML",
        idempotency_key="memory:commitment",
    )
    api.crm_interaction_log(
        contact="Илья",
        kind="agreement",
        summary="Договорились проверить OAuth",
        project="Hub_ML",
        idempotency_key="memory:crm",
    )
    api.crm_interaction_log(
        contact="Анна",
        kind="call",
        summary="Просто созвонились",
        idempotency_key="memory:call",
    )

    first = api.memory_consolidate()
    replay = api.memory_consolidate()
    snapshots = api.memory_consolidation_list()["items"]

    assert first["status"] == "updated"
    assert replay["status"] == "no_change"
    assert [item["scope"] for item in snapshots] == ["global", "Hub_ML"]
    facts = [fact for snapshot in snapshots for fact in snapshot["facts"]]
    assert {item["kind"] for item in facts} == {"preference", "commitment", "agreement"}
    assert all("неподтверждённая" not in item["text"] for item in facts)
    assert all("Просто созвонились" not in item["text"] for item in facts)


def test_consolidation_keeps_projects_separate(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    api.memory_block_upsert(
        block_type="project",
        subject="stack",
        content="Streamlit и pytest",
        project="Hub_ML",
    )
    api.memory_block_upsert(
        block_type="project",
        subject="reader",
        content="Telegram Mini App",
        project="Reader",
    )

    api.memory_consolidate()
    snapshots = api.memory_consolidation_list()["items"]

    assert [item["scope"] for item in snapshots] == ["Hub_ML", "Reader"]
