from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from hermes.native_tools.action_plans import ActionPlanStore
from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.personal_os import PersonalOSStore


ROOT = Path(__file__).resolve().parents[1]


def test_memory_blocks_keep_domains_separate_and_filter_by_project(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    profile = api.memory_block_upsert(
        block_type="profile",
        subject="mikhail",
        content="Предпочитает короткие ответы.",
    )
    promise = api.memory_block_upsert(
        block_type="commitment",
        subject="oauth-review",
        content="Проверить OAuth до пятницы.",
        project="Hub_ML",
    )

    assert profile["block_type"] == "profile"
    assert promise["project"] == "Hub_ML"
    assert api.memory_block_list(block_type="commitment", project="Hub_ML") == {"items": [promise]}


def test_memory_block_upsert_updates_same_subject_without_duplicate(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    first = api.memory_block_upsert(
        block_type="preference",
        subject="response-style",
        content="Коротко.",
    )
    updated = api.memory_block_upsert(
        block_type="preference",
        subject="response-style",
        content="Коротко и живо.",
    )

    assert updated["id"] == first["id"]
    assert updated["content"] == "Коротко и живо."
    assert len(api.memory_block_list(block_type="preference")["items"]) == 1


def test_notes_support_search_edit_history_and_delete(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    note = api.memory_block_upsert(
        block_type="note",
        subject="OAuth",
        content="Проверить refresh token перед релизом.",
        project="Hub_ML",
    )

    assert api.note_search(query="refresh token", project="Hub_ML")["items"][0]["id"] == note["id"]
    edited = api.note_edit(note_id=note["id"], content="Проверить rotation refresh token перед релизом.")

    assert edited["content"].startswith("Проверить rotation")
    assert api.note_history(note_id=note["id"])["items"][0]["content"] == "Проверить refresh token перед релизом."
    assert api.note_delete(note_id=note["id"])["status"] == "deleted"
    assert api.note_search(query="refresh") == {"items": []}


def test_memory_context_is_small_and_marks_old_facts_without_hiding_them(tmp_path: Path) -> None:
    database = tmp_path / "personal-os.sqlite3"
    api = NativeToolsAPI(database_path=database)
    api.memory_block_upsert(
        block_type="preference",
        subject="Стиль",
        content="Пиши коротко.",
    )
    api.memory_block_upsert(
        block_type="note",
        subject="OAuth",
        content="Проверить refresh token.",
        project="Hub_ML",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE memory_blocks SET updated_at = '2000-01-01 00:00:00' WHERE subject = 'Стиль'"
        )

    context = api.memory_context(query="OAuth", project="Hub_ML", limit=99)
    old_context = api.memory_context(limit=2)
    preference_context = api.memory_context(query="коротко")

    assert len(context["items"]) == 1
    assert context["items"][0]["subject"] == "OAuth"
    assert context["items"][0]["stale"] is False
    assert len(old_context["items"]) == 2
    assert any(item["stale"] is True for item in old_context["items"])
    assert "могут устареть" in old_context["freshness_note"]
    assert preference_context["items"][0]["subject"] == "Стиль"


def test_project_context_resolves_alias_and_returns_scoped_integrations(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    created = api.project_context_upsert(
        key="hub-ml",
        name="Hub_ML",
        aliases=["хаб мл", "обучение ml"],
        trello_board="ML Board",
        trello_list="Today",
        calendar_id="ml-calendar@example.test",
        contacts=["Илья"],
        tools=["tasks", "calendar", "notes"],
        context_note="Практика и менторинг по ML.",
    )

    resolved = api.project_context_resolve(text="Что сегодня по хаб мл?")

    assert resolved == created
    assert resolved["trello_board"] == "ML Board"
    assert resolved["tools"] == ["tasks", "calendar", "notes"]
    assert api.project_context_list() == {"items": [created]}


def test_project_context_rejects_unknown_tool_capability(tmp_path: Path) -> None:
    store = PersonalOSStore(tmp_path / "personal-os.sqlite3")

    with pytest.raises(ValueError, match="allowlist"):
        store.upsert_project(key="unsafe", name="Unsafe", tools=["root_shell"])


def test_profile_exposes_personal_os_only_through_native_mcp() -> None:
    config = (ROOT / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (ROOT / "hermes" / "skills" / "personal-memory" / "SKILL.md").read_text(encoding="utf-8")

    for tool in (
        "memory_block_upsert",
        "memory_block_list",
        "note_search",
        "note_edit",
        "note_history",
        "note_delete_confirmed",
        "project_context_upsert",
        "project_context_list",
        "project_context_resolve",
    ):
        assert f"- {tool}" in config
    assert "mcp_jarhert_native_memory_block_upsert" in skill
    assert "native_tools/cli.py" not in skill


def test_completion_stats_aggregates_done_tasks_into_day_buckets(tmp_path: Path) -> None:
    database = tmp_path / "personal-os.sqlite3"
    ActionPlanStore(database)
    api = NativeToolsAPI(database_path=database)
    with sqlite3.connect(database) as connection:
        for key, finished_at in [
            ("plan-today", "2026-07-17 10:00:00"),
            ("plan-yesterday", "2026-07-16 09:30:00"),
        ]:
            plan_id = int(
                connection.execute(
                    "INSERT INTO action_plans(status, idempotency_key, finished_at) VALUES ('succeeded', ?, ?)",
                    (key, finished_at),
                ).lastrowid
            )
            connection.execute(
                """
                INSERT INTO plan_actions(plan_id, position, node_key, action_type, payload_json, status)
                VALUES (?, 0, 'a1', 'task.done', '{"title": "Задача"}', 'succeeded')
                """,
                (plan_id,),
            )

    stats = api.completion_stats(now="2026-07-17T23:00:00+03:00", timezone_name="Europe/Moscow", days=7)

    assert stats["done_today"] == 1
    assert stats["streak"] == 2
    assert len(stats["daily"]) == 7
    by_day = {entry["date"]: entry["done"] for entry in stats["daily"]}
    assert by_day["2026-07-17"] == 1
    assert by_day["2026-07-16"] == 1
    assert by_day["2026-07-15"] == 0
