import asyncio
from pathlib import Path

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.task_calendar import TaskCalendarHealth
from hermes.native_tools.telegram_text_export import ExportResult


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
    assert calls == [{"peer": "@example", "output_format": "txt", "limit": 10}]


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
