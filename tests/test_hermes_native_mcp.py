from pathlib import Path

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.mcp_server import TOOLS, handle_message
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


def test_native_mcp_exposes_only_explicit_tools() -> None:
    response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response is not None
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == set(TOOLS)
    assert "shell" not in names
    assert "file_read" not in names

    action_items = TOOLS["action_plan_create"]["inputSchema"]["properties"]["actions"]["items"]["oneOf"]
    assert {item["properties"]["type"]["const"] for item in action_items} == {
        "task.create",
        "task.move",
        "task.done",
        "task.delete",
        "calendar.create",
        "calendar.move",
        "calendar.delete",
    }


def test_native_api_plan_round_trip_and_health_redaction(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=FakeAdapter)

    health = api.integration_health()
    draft = api.action_plan_create(
        actions=[{"type": "task.create", "payload": {"title": "MCP canary"}}],
        idempotency_key="telegram-update-1",
    )
    api.action_plan_approve(plan_id=draft["id"])
    completed = api.action_plan_execute(plan_id=draft["id"])

    assert health == {"ok": True, "trello_ok": True, "calendar_ok": True}
    assert completed["status"] == "succeeded"
    assert completed["actions"][0]["result_meta"] == {"trello_card_id": "abc123"}


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
    result = api.telegram_text_export(peer="@example", confirmed=True, limit=10)

    assert result["message_count"] == 3
    assert calls == [{"peer": "@example", "output_format": "txt", "limit": 10}]


def test_profile_uses_native_mcp_instead_of_terminal_allowlist() -> None:
    config = (ROOT / "hermes" / "config.yaml").read_text(encoding="utf-8")

    assert "mcp_servers:\n  jarhert_native:" in config
    assert "${HERMES_HOME}/.venv/bin/python" in config
    assert "command_allowlist:" not in config
    assert "TELEGRAM_BOT_TOKEN:" not in config.split("mcp_servers:", 1)[1]
