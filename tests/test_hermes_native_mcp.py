import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.task_calendar import TaskCalendarHealth
from hermes.native_tools.telegram_text_export import ExportResult, FileDownloadItem, FileDownloadResult


ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    def health_check(self) -> TaskCalendarHealth:
        return TaskCalendarHealth(True, "private trello detail", True, "private calendar detail")

    def list_tasks(self, *, list_name: str | None = None) -> str:
        return f"tasks:{list_name or 'all'}"

    def list_calendar_events(self, *, when: str = "today") -> str:
        return f"calendar:{when}"

    def create_task(self, **payload: object) -> str:
        return f"created {payload['title']}\ntrello_card_id=abc123"


def test_native_api_reuses_task_calendar_adapter(tmp_path: Path) -> None:
    created: list[FakeAdapter] = []

    def factory() -> FakeAdapter:
        adapter = FakeAdapter()
        created.append(adapter)
        return adapter

    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=factory)

    api.integration_health()
    api.task_list(list_name="Today")
    api.calendar_list(when="today")

    assert len(created) == 1


def test_native_api_plan_round_trip_and_health_redaction(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)
    previews: list[str] = []

    async def confirm(preview: str) -> bool:
        previews.append(preview)
        return True

    health = api.integration_health()
    completed = asyncio.run(
        api.action_plan_confirm_execute(
            actions=[{"type": "task.create", "payload": {"title": "MCP canary"}}],
            idempotency_key="telegram-update-1",
            confirmer=confirm,
        )
    )

    assert health == {"ok": True, "trello_ok": True, "calendar_ok": True}
    assert completed["status"] == "succeeded"
    assert completed["actions"][0]["result_meta"] == {"trello_card_id": "abc123"}
    assert previews == ["1. task.create: MCP canary"]


def test_owner_autonomy_executes_routine_plan_without_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_OWNER_AUTONOMY", "true")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)
    previews: list[str] = []

    async def confirm(preview: str) -> bool:
        previews.append(preview)
        return False

    completed = asyncio.run(
        api.action_plan_confirm_execute(
            actions=[{"type": "task.create", "payload": {"title": "Owner routine"}}],
            idempotency_key="telegram-owner-autonomy-1",
            confirmer=confirm,
        )
    )

    assert completed["status"] == "succeeded"
    assert completed["actions"][0]["status"] == "succeeded"
    assert previews == []


def test_owner_autonomy_keeps_high_risk_plan_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_OWNER_AUTONOMY", "true")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)
    previews: list[str] = []

    async def decline(preview: str) -> bool:
        previews.append(preview)
        return False

    result = asyncio.run(
        api.action_plan_confirm_execute(
            actions=[{"type": "task.delete", "payload": {"title": "Do not delete"}}],
            idempotency_key="telegram-owner-autonomy-delete",
            confirmer=decline,
        )
    )

    assert result["status"] == "cancelled"
    assert previews == ["1. task.delete: Do not delete"]


def test_confirmed_plan_delivers_one_owner_receipt_without_replaying_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    monkeypatch.setenv("HERMES_ACTION_PLAN_RECEIPT_DELIVERY", "true")
    delivered: list[tuple[int, str]] = []
    api = NativeToolsAPI(
        database_path=tmp_path / "personal.sqlite3",
        adapter_factory=FakeAdapter,
        plan_receipt_sender=lambda chat_id, text: delivered.append((chat_id, text)) or "telegram:1",
    )

    async def confirm(_preview: str) -> bool:
        return True

    first = asyncio.run(
        api.action_plan_confirm_execute(
            actions=[{"type": "task.create", "payload": {"title": "One receipt"}}],
            idempotency_key="telegram-update-receipt-1",
            confirmer=confirm,
        )
    )
    replay = asyncio.run(
        api.action_plan_confirm_execute(
            actions=[{"type": "task.create", "payload": {"title": "One receipt"}}],
            idempotency_key="telegram-update-receipt-1",
            confirmer=confirm,
        )
    )

    assert first["status"] == replay["status"] == "succeeded"
    assert delivered == [(566055009, f"Готово: план #{first['id']} выполнен.")]


def test_native_api_exposes_compact_plan_trace_and_tool_discovery(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)
    plan = api.action_plan_create(
        actions=[{"type": "task.create", "payload": {"title": "Проверить релиз"}}],
        idempotency_key="trace-and-catalog",
    )

    trace = api.action_plan_trace(plan_id=plan["id"])
    catalog = api.tool_catalog_discover(query="задача", limit=4)

    assert trace["next"] == {"key": "action-1", "type": "task.create", "title": "Проверить релиз"}
    assert trace["pending"] == 1
    assert 1 <= len(catalog["items"]) <= 4
    assert all("input_contract" in item and "output_contract" in item for item in catalog["items"])


def test_native_api_cancelled_plan_has_no_side_effect(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)

    async def decline(_preview: str) -> bool:
        return False

    result = asyncio.run(
        api.action_plan_confirm_execute(
            actions=[{"type": "task.create", "payload": {"title": "Do not create"}}],
            idempotency_key="telegram-update-2",
            confirmer=decline,
        )
    )

    assert result["status"] == "cancelled"
    assert result["actions"][0]["status"] == "pending"


def test_confirmed_plan_execution_does_not_block_the_event_loop(tmp_path: Path) -> None:
    class SlowAdapter(FakeAdapter):
        def create_task(self, **payload: object) -> str:
            time.sleep(0.3)
            return super().create_task(**payload)

    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=SlowAdapter)

    async def confirm(_preview: str) -> bool:
        return True

    async def scenario() -> tuple[dict[str, object], int]:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            completed = await api.action_plan_confirm_execute(
                actions=[{"type": "task.create", "payload": {"title": "Slow plan"}}],
                idempotency_key="slow-plan-loop",
                confirmer=confirm,
            )
        finally:
            ticker_task.cancel()
            await asyncio.gather(ticker_task, return_exceptions=True)
        return completed, ticks

    completed, ticks = asyncio.run(scenario())

    assert completed["status"] == "succeeded"
    assert ticks >= 2


def test_native_api_executes_one_confirmed_dependency_plan(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)
    previews: list[str] = []

    async def confirm(preview: str) -> bool:
        previews.append(preview)
        return True

    result = asyncio.run(
        api.action_plan_dag_confirm_execute(
            nodes=[
                {"key": "note", "type": "note.save", "payload": {"subject": "OAuth", "content": "Проверить refresh"}},
                {
                    "key": "task",
                    "type": "task.create",
                    "payload": {"title": "Проверить refresh"},
                    "depends_on": ["note"],
                },
            ],
            idempotency_key="telegram-update-dag-1",
            confirmer=confirm,
        )
    )

    assert result["status"] == "succeeded"
    assert len(previews) == 1
    assert result["actions"][1]["depends_on_action_ids"] == (result["actions"][0]["id"],)


def test_native_api_export_requires_confirmation(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def exporter(**kwargs: object) -> ExportResult:
        calls.append(kwargs)
        return ExportResult(
            path=tmp_path / "chat.txt",
            peer=str(kwargs["peer"]),
            title="Chat",
            message_count=3,
            output_format=str(kwargs["output_format"]),
            truncated=False,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        )

    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", exporter=exporter)

    with pytest.raises(ValueError, match="подтверждение"):
        api.telegram_text_export(peer="@example", confirmed=False)

    async def confirm(preview: str) -> bool:
        return "@example" in preview

    result = asyncio.run(
        api.telegram_text_export_confirmed(
            peer="@example",
            limit=10,
            confirmer=confirm,
        )
    )

    assert result["message_count"] == 3
    assert result["expires_at"]
    assert result["attachment"]["directive"].startswith("[[as_document]]\nMEDIA:")
    assert calls == [{"peer": "@example", "output_format": "txt", "limit": 10}]


def test_native_api_downloads_chat_files_only_after_one_confirmation(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def downloader(**kwargs: object) -> FileDownloadResult:
        calls.append(kwargs)
        path = tmp_path / "telegram_file_1_20300101_120000_report.txt"
        path.write_text("report", encoding="utf-8")
        return FileDownloadResult(
            peer=str(kwargs["peer"]),
            title="Chat",
            items=(
                FileDownloadItem(
                    message_id=1,
                    path=path,
                    name=path.name,
                    size_bytes=path.stat().st_size,
                    mime_type="text/plain",
                ),
            ),
            skipped_oversized=0,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        )

    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", file_downloader=downloader)

    with pytest.raises(ValueError, match="подтверждение"):
        api.telegram_file_download(peer="@example", confirmed=False)

    async def confirm(preview: str) -> bool:
        return "@example" in preview

    result = asyncio.run(
        api.telegram_file_download_confirmed(
            peer="@example", file_limit=3, scan_limit=100, confirmer=confirm
        )
    )

    assert result["status"] == "ok"
    assert result["expires_at"]
    assert result["items"][0]["attachment"]["directive"].startswith("[[as_document]]\nMEDIA:")
    assert calls == [{"peer": "@example", "file_limit": 3, "scan_limit": 100, "message_ids": None}]


def test_native_api_can_queue_explicit_export_analysis_and_keeps_raw_text_off_the_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    source = export_dir / "test_20300101_120000.txt"
    source.write_text("[1] 2030-01-01T12:00:00+00:00\nАвтор: ML мысль\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_EXPORT_DIR", str(export_dir))
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    excerpt = api.telegram_text_export_excerpt(path=str(source))
    queued = api.telegram_text_export_queue_analysis(
        path=str(source),
        question="Выдели темы и полезные идеи.",
        idempotency_key="telegram:export:analysis:1",
    )

    assert "ML мысль" in excerpt["text"]
    assert queued["mode"] == "research"
    assert queued["source_text"].startswith("[1]")
    summary = api.coding_job_list()["items"][0]
    assert "source_text" not in summary
    assert summary["source_label"] == source.name


def test_native_api_exposes_contacts_and_idempotent_message_plan(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")
    api.contact_add(name="Илья", telegram_chat_id=123, aliases=["Илье"])
    confirmations: list[str] = []

    async def confirm(preview: str) -> bool:
        confirmations.append(preview)
        return True

    items = [{"contact": "Илье", "text": "Созвон завтра", "send_at": "2030-01-02T12:00:00+03:00"}]
    first = asyncio.run(
        api.message_plan_confirm_schedule(
            items=items,
            idempotency_key="telegram-update-contacts-1",
            confirmer=confirm,
        )
    )
    replay = asyncio.run(
        api.message_plan_confirm_schedule(
            items=items,
            idempotency_key="telegram-update-contacts-1",
            confirmer=confirm,
        )
    )

    assert api.contact_list() == {
        "items": [{"id": 1, "name": "Илья", "telegram_chat_id": 123, "aliases": ["Илья", "Илье"]}]
    }
    assert first["status"] == "scheduled"
    assert replay == first
    assert len(confirmations) == 1
    assert "Илья" in confirmations[0]


def test_native_api_cancelled_message_plan_stays_cancelled(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")
    api.contact_add(name="Илья", telegram_chat_id=123, aliases=[])

    async def decline(_preview: str) -> bool:
        return False

    result = asyncio.run(
        api.message_plan_confirm_schedule(
            items=[{"contact": "Илья", "text": "Не отправлять", "send_at": "2030-01-02T12:00:00+03:00"}],
            idempotency_key="telegram-update-contacts-2",
            confirmer=decline,
        )
    )

    assert result["status"] == "cancelled"
    assert {item["status"] for item in result["messages"]} == {"cancelled"}


def test_native_api_exposes_github_monitors(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    created = api.monitor_add_github_releases(
        name="codex-releases",
        owner="openai",
        repo="codex",
        condition="Только важные релизы",
    )
    disabled = api.monitor_disable(monitor_id=created["id"])

    assert created["source_type"] == "github_releases"
    assert disabled["enabled"] is False
    assert api.monitor_list() == {"items": [disabled]}


def test_profile_uses_native_mcp_instead_of_terminal_allowlist() -> None:
    config = (ROOT / "hermes" / "config.yaml").read_text(encoding="utf-8")
    contact_skill = (ROOT / "hermes" / "skills" / "contact-messaging" / "SKILL.md").read_text(encoding="utf-8")
    monitor_skill = (ROOT / "hermes" / "skills" / "event-monitors" / "SKILL.md").read_text(encoding="utf-8")

    assert "mcp_servers:\n  jarhert_native:" in config
    assert "${HERMES_HOME}/.venv/bin/python" in config
    assert "native_tools/mcp_runtime.py" in config
    assert "action_plan_confirm_execute" in config
    assert "action_plan_dag_confirm_execute" in config
    assert "action_plan_status" in config
    assert "action_plan_pause_confirmed" in config
    assert "action_plan_resume_confirmed" in config
    assert "skill_mark_staged" in config
    assert "telegram_text_export_confirmed" in config
    assert "contact_add" in config
    assert "contact_list" in config
    assert "message_plan_confirm_schedule" in config
    assert "message_plan_cancel_confirmed" in config
    assert "monitor_add_github_releases" in config
    assert "monitor_list" in config
    assert "monitor_disable" in config
    assert "action_plan_execute" not in config
    assert "command_allowlist:" not in config
    assert "TELEGRAM_BOT_TOKEN:" not in config.split("mcp_servers:", 1)[1]
    assert "mcp_jarhert_native_message_plan_confirm_schedule" in contact_skill
    assert "mcp_jarhert_native_monitor_add_github_releases" in monitor_skill
    assert "native_tools/cli.py" not in contact_skill
    assert "native_tools/cli.py" not in monitor_skill


def test_coding_confirmation_is_owned_by_one_native_mcp_prompt() -> None:
    soul = (ROOT / "hermes" / "SOUL.md").read_text(encoding="utf-8")
    skill = (ROOT / "hermes" / "skills" / "sandboxed-coding" / "SKILL.md").read_text(encoding="utf-8")

    assert "Не пиши отдельный preview от себя" in soul
    assert "Do not compose a separate preview in chat" in skill
