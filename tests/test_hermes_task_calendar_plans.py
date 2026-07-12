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


def test_plan_can_pause_resume_without_losing_pending_actions(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "plans.sqlite3")
    plan = store.create(
        [{"type": "task.create", "payload": {"title": "Продолжить позже"}}],
        idempotency_key="pause-resume",
    )
    store.approve(plan.id)

    paused = store.pause(plan.id)
    resumed = store.resume(plan.id)

    assert paused.status == "paused"
    assert resumed.status == "approved"
    assert resumed.actions[0].status == "pending"


def test_dag_waits_for_dependencies_and_keeps_checkpoints(tmp_path) -> None:
    class DagAdapter(FakeAdapter):
        def save_note(self, **payload):
            self.calls.append(("note.save", payload))
            return "note_id=note-1"

    store = ActionPlanStore(tmp_path / "dag.sqlite3")
    nodes = [
        {"key": "note", "type": "note.save", "payload": {"subject": "Идея", "content": "Собрать план"}},
        {"key": "task", "type": "task.create", "payload": {"title": "Собрать план"}, "depends_on": ["note"]},
    ]
    plan = store.create_dag(nodes, idempotency_key="dag-1")
    replay = store.create_dag(nodes, idempotency_key="dag-1")
    store.approve(plan.id)

    result = execute_plan(store, plan.id, DagAdapter())

    assert replay.id == plan.id
    assert result.status == "succeeded"
    assert [action.node_key for action in result.actions] == ["note", "task"]
    assert result.actions[1].depends_on_action_ids == (result.actions[0].id,)


def test_dag_blocks_child_when_parent_fails(tmp_path) -> None:
    class FailingAdapter(FakeAdapter):
        def create_task(self, **payload):
            raise RuntimeError("Trello unavailable")

    store = ActionPlanStore(tmp_path / "dag-failed.sqlite3")
    plan = store.create_dag(
        [
            {"key": "parent", "type": "task.create", "payload": {"title": "Parent"}},
            {
                "key": "child",
                "type": "calendar.create",
                "payload": {"title": "Child", "start": "2030-01-02 12:00", "end": "2030-01-02 12:30"},
                "depends_on": ["parent"],
            },
        ],
        idempotency_key="dag-failed",
    )
    store.approve(plan.id)

    result = execute_plan(store, plan.id, FailingAdapter())

    assert result.status == "failed"
    assert [item.status for item in result.actions] == ["failed", "failed"]
    assert "Зависимость" in str(result.actions[1].error)


def test_dag_rejects_forward_dependency(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "dag-invalid.sqlite3")

    with pytest.raises(ActionPlanError, match="Зависимость"):
        store.create_dag(
            [
                {"key": "child", "type": "task.create", "payload": {"title": "Child"}, "depends_on": ["parent"]},
                {"key": "parent", "type": "task.create", "payload": {"title": "Parent"}},
            ],
            idempotency_key="dag-invalid",
        )


def test_external_actions_use_one_batch_and_keep_per_action_results(tmp_path) -> None:
    store = ActionPlanStore(tmp_path / "plans.sqlite3")
    plan = store.create(
        [
            {"type": "task.create", "payload": {"title": "Задача"}},
            {
                "type": "calendar.create",
                "payload": {"title": "Созвон", "start": "2030-01-02 12:00", "end": "2030-01-02 12:30"},
            },
        ],
        idempotency_key="batch-plan",
    )
    store.approve(plan.id)

    class BatchAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def execute_batch(self, actions):
            self.calls += 1
            assert [item["type"] for item in actions] == ["task.create", "calendar.create"]
            return [
                {"ok": True, "result": "trello_card_id=task-1"},
                {"ok": False, "error": "Calendar timeout"},
            ]

    adapter = BatchAdapter()
    result = execute_plan(store, plan.id, adapter)

    assert adapter.calls == 1
    assert [item.status for item in result.actions] == ["succeeded", "failed"]
    assert result.status == "partial"
