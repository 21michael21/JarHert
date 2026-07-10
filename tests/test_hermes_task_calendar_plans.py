from __future__ import annotations

import pytest

from hermes.native_tools.action_plans import ActionPlanStore, ActionPlanError, execute_plan


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def create_task(self, **payload):
        self.calls.append(("task.create", payload))
        return "Created\ntrello_card_id=card123\nhttps://trello.com/c/test"

    def create_calendar_event(self, **payload):
        self.calls.append(("calendar.create", payload))
        return "Created\ncalendar_event_id=event456"

    def delete_task(self, **payload):
        self.calls.append(("task.delete", payload))
        return "Deleted"


def test_one_approval_executes_complete_plan_once(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "personal-os.sqlite3")
    actions = [
        {"type": "task.create", "payload": {"title": "Проверить релиз", "list_name": "Today"}},
        {
            "type": "calendar.create",
            "payload": {
                "title": "Проверить релиз",
                "start": "2030-01-02 12:00",
                "end": "2030-01-02 12:30",
            },
        },
    ]
    plan = store.create(actions, idempotency_key="telegram-update-10")
    replay = store.create(actions, idempotency_key="telegram-update-10")
    adapter = FakeAdapter()

    store.approve(plan.id)
    result = execute_plan(store, plan.id, adapter)
    second_execution = execute_plan(store, plan.id, adapter)

    assert replay.id == plan.id
    assert result.status == "succeeded"
    assert second_execution.status == "succeeded"
    assert [call[0] for call in adapter.calls] == ["task.create", "calendar.create"]
    assert result.actions[0].result_meta["trello_card_id"] == "card123"
    assert result.actions[1].result_meta["calendar_event_id"] == "event456"


def test_unapproved_plan_cannot_execute(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "personal-os.sqlite3")
    plan = store.create(
        [{"type": "task.delete", "payload": {"title": "Не удалять"}}],
        idempotency_key="draft",
    )

    with pytest.raises(ActionPlanError, match="подтверждён"):
        execute_plan(store, plan.id, FakeAdapter())


def test_unknown_action_and_bad_payload_are_rejected(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "personal-os.sqlite3")

    with pytest.raises(ActionPlanError, match="allowlist"):
        store.create([{"type": "shell", "payload": {"command": "rm"}}], idempotency_key="bad")

    with pytest.raises(ActionPlanError, match="title"):
        store.create([{"type": "task.create", "payload": {}}], idempotency_key="missing")


def test_partial_failure_is_visible_and_later_action_continues(tmp_path) -> None:
    class PartialAdapter(FakeAdapter):
        def create_task(self, **payload):
            self.calls.append(("task.create", payload))
            raise RuntimeError("Trello unavailable")

    store = ActionPlanStore(tmp_path / "personal-os.sqlite3")
    plan = store.create(
        [
            {"type": "task.create", "payload": {"title": "Task"}},
            {
                "type": "calendar.create",
                "payload": {"title": "Event", "start": "2030-01-02 12:00", "end": "2030-01-02 12:30"},
            },
        ],
        idempotency_key="partial",
    )
    store.approve(plan.id)

    result = execute_plan(store, plan.id, PartialAdapter())

    assert result.status == "partial"
    assert [action.status for action in result.actions] == ["failed", "succeeded"]


def test_cancelled_plan_cannot_be_approved(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "personal-os.sqlite3")
    plan = store.create(
        [{"type": "task.delete", "payload": {"title": "Task"}}],
        idempotency_key="cancel",
    )
    store.cancel(plan.id)

    with pytest.raises(ActionPlanError, match="cancelled"):
        store.approve(plan.id)
