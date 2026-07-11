from __future__ import annotations

import asyncio
from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


class FakeTaskCalendarAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def create_task(self, **payload: object) -> str:
        self.calls.append(("task", payload))
        return "created task\ntrello_card_id=task-123"

    def create_calendar_event(self, **payload: object) -> str:
        self.calls.append(("calendar", payload))
        return "created event\ncalendar_event_id=event-123"


def test_one_inbox_plan_saves_note_commitment_task_and_meeting_once(tmp_path: Path) -> None:
    adapter = FakeTaskCalendarAdapter()
    api = NativeToolsAPI(
        database_path=tmp_path / "personal-os.sqlite3",
        adapter_factory=lambda: adapter,
    )
    confirmations: list[str] = []

    async def confirm(preview: str) -> bool:
        confirmations.append(preview)
        return True

    actions = [
        {
            "type": "note.save",
            "payload": {"subject": "OAuth", "content": "Проверить refresh flow", "project": "Hub_ML"},
        },
        {
            "type": "commitment.create",
            "payload": {
                "subject": "Ответить Илье",
                "content": "Отправить результаты ревью",
                "contact": "Илья",
                "project": "Hub_ML",
                "due_at": "2030-01-05T12:00:00+03:00",
            },
        },
        {"type": "task.create", "payload": {"title": "Проверить модель", "list_name": "Today"}},
        {
            "type": "calendar.create",
            "payload": {
                "title": "Созвон по ML",
                "start": "2030-01-05T15:00:00+03:00",
                "end": "2030-01-05T15:30:00+03:00",
            },
        },
    ]

    first = asyncio.run(
        api.action_plan_confirm_execute(
            actions=actions,
            idempotency_key="telegram-voice-100",
            confirmer=confirm,
        )
    )
    replay = asyncio.run(
        api.action_plan_confirm_execute(
            actions=actions,
            idempotency_key="telegram-voice-100",
            confirmer=confirm,
        )
    )

    assert first["status"] == "succeeded"
    assert replay == first
    assert len(confirmations) == 1
    assert [name for name, _payload in adapter.calls] == ["task", "calendar"]
    assert api.memory_block_list(block_type="note", project="Hub_ML")["items"][0]["content"] == "Проверить refresh flow"
    commitments = api.commitment_list(contact="Илья", status="open")["items"]
    assert len(commitments) == 1
    assert commitments[0]["subject"] == "Ответить Илье"
    assert commitments[0]["due_at"] == "2030-01-05T09:00:00+00:00"
    assert api.reminder_list()["items"][0]["source_id"] == commitments[0]["id"]


def test_commitment_can_be_completed_and_no_longer_appears_open(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    created = api.commitment_create(
        subject="Позвонить Илье",
        content="Обсудить проект",
        contact="Илья",
        project="Hub_ML",
        due_at="2030-01-05T12:00:00+03:00",
    )

    completed = api.commitment_complete(commitment_id=created["id"])

    assert completed["status"] == "done"
    assert api.commitment_list(contact="Илья", status="open") == {"items": []}
