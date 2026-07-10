from __future__ import annotations

from dataclasses import replace

import pytest

from assistant.action_executor import ActionExecutor
from assistant.action_schema import ActionType, PlannedAction
from assistant.agent_jobs import InMemoryAgentJobStore
from assistant.google_docs_sync import NullDocsSync
from assistant.ideas import InMemoryIdeaStore
from assistant.memory import InMemoryMemoryStore
from assistant.task_command_center import TaskCommandError
from assistant.tool_registry import (
    ToolContext,
    ToolExecutionError,
    ToolRisk,
    build_default_tool_registry,
)
from assistant.types import UserContext
from reminders.store import InMemoryReminderStore


class FakeTaskCenter:
    def __init__(self) -> None:
        self.calls = []

    def create_task(self, text):
        self.calls.append(("create_task", text))
        return "task created card_id=trello123456 https://trello.com/c/abcd1234/task"

    def create_task_with_calendar(self, **kwargs):
        self.calls.append(("create_task_with_calendar", kwargs))
        return "task and calendar created card_id=trello123456 calendar_event_id=event123456"

    def list_tasks(self, text):
        self.calls.append(("list_tasks", text))
        return "task list"

    def move_task(self, text):
        self.calls.append(("move_task", text))
        return "task moved"

    def complete_task(self, text):
        self.calls.append(("complete_task", text))
        return "task done"

    def delete_task(self, text):
        self.calls.append(("delete_task", text))
        return "task deleted"

    def create_calendar_event(self, text):
        self.calls.append(("create_calendar_event", text))
        return "calendar event calendar_event_id=event123456"

    def list_calendar_events(self, text):
        self.calls.append(("list_calendar_events", text))
        return "calendar list"

    def move_calendar_event(self, text):
        self.calls.append(("move_calendar_event", text))
        return "calendar moved calendar_event_id=event123456"

    def delete_calendar_event(self, text):
        self.calls.append(("delete_calendar_event", text))
        return "calendar deleted calendar_event_id=event123456"


def make_context(task_center=None) -> ToolContext:
    return ToolContext(
        user=UserContext(user_id=1, tg_user_id=1001),
        memories=InMemoryMemoryStore(),
        ideas=InMemoryIdeaStore(),
        reminders=InMemoryReminderStore(),
        docs_sync=NullDocsSync(),
        task_center=task_center,
        agent_jobs=InMemoryAgentJobStore(),
    )


def test_default_registry_has_only_safe_allowlisted_tools() -> None:
    registry = build_default_tool_registry()

    names = {tool.name.value for tool in registry.list_tools()}

    assert {
        "idea.save",
        "memory.save",
        "reminder.create",
        "task.create",
        "task.list",
        "task.move",
        "task.done",
        "task.delete",
        "calendar.create",
        "calendar.list",
        "calendar.move",
        "calendar.delete",
        "telegram.reply",
    }.issubset(names)
    assert "shell.run" not in names
    assert "file.write" not in names
    assert "server.exec" not in names
    for tool in registry.list_tools():
        assert tool.input_schema is not None
        assert tool.timeout_seconds > 0
        assert tool.risk in {ToolRisk.LOW, ToolRisk.MEDIUM, ToolRisk.HIGH}
        assert tool.retryable_errors or tool.permanent_errors


def test_executor_runs_idea_tool_without_knowing_store_details() -> None:
    context = make_context()
    executor = ActionExecutor(build_default_tool_registry())

    result = executor.execute(
        PlannedAction(ActionType.IDEA_SAVE, {"text": "проверить action registry"}),
        context,
    )

    assert "Сохранил идею #1" in result.message
    assert context.ideas.list_for_user(1)[0].text == "проверить action registry"


def test_executor_routes_task_create_to_registered_tool() -> None:
    task_center = FakeTaskCenter()
    context = make_context(task_center=task_center)
    executor = ActionExecutor(build_default_tool_registry())

    result = executor.execute(
        PlannedAction(
            ActionType.TASK_CREATE,
            {"title": "проверить Trello", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        ),
        context,
    )

    assert "Создал задачу" in result.message
    assert task_center.calls == [
        (
            "create_task_with_calendar",
            {"title": "проверить Trello", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        )
    ]
    assert result.meta["trello_card_id"] == "trello123456"
    assert result.meta["calendar_event_id"] == "event123456"


def test_tool_result_keeps_root_idempotency_key_with_external_ids() -> None:
    context = replace(
        make_context(task_center=FakeTaskCenter()),
        idempotency_key="telegram:1001:4242:action:1",
    )
    executor = ActionExecutor(build_default_tool_registry())

    result = executor.execute(
        PlannedAction(ActionType.TASK_CREATE, {"title": "проверить Trello"}),
        context,
    )

    assert result.meta["idempotency_key"] == "telegram:1001:4242:action:1"
    assert result.meta["trello_card_id"] == "trello123456"


def test_tool_registry_classifies_external_task_errors() -> None:
    class BrokenTaskCenter(FakeTaskCenter):
        def create_task(self, text):
            raise TaskCommandError("network timeout")

    context = make_context(task_center=BrokenTaskCenter())
    executor = ActionExecutor(build_default_tool_registry())

    with pytest.raises(ToolExecutionError) as exc:
        executor.execute(PlannedAction(ActionType.TASK_CREATE, {"title": "проверить"}), context)

    assert exc.value.retryable
    assert exc.value.kind == "retryable"


def test_tool_requires_configured_task_center() -> None:
    executor = ActionExecutor(build_default_tool_registry())

    with pytest.raises(ToolExecutionError) as exc:
        executor.execute(PlannedAction(ActionType.TASK_LIST, {"list": "Today"}), make_context())

    assert not exc.value.retryable
    assert exc.value.kind == "permanent"


def test_executor_routes_task_delete_to_registered_tool() -> None:
    task_center = FakeTaskCenter()
    context = make_context(task_center=task_center)
    executor = ActionExecutor(build_default_tool_registry())

    result = executor.execute(PlannedAction(ActionType.TASK_DELETE, {"title": "проверить сервер"}), context)

    assert "Удалил задачу" in result.message
    assert task_center.calls == [("delete_task", "проверить сервер")]


def test_executor_routes_calendar_crud_to_registered_tools() -> None:
    task_center = FakeTaskCenter()
    context = make_context(task_center=task_center)
    executor = ActionExecutor(build_default_tool_registry())

    listed = executor.execute(PlannedAction(ActionType.CALENDAR_LIST, {"when": "today"}), context)
    moved = executor.execute(
        PlannedAction(ActionType.CALENDAR_MOVE, {"title": "созвон", "start": "tomorrow 10:00", "end": "tomorrow 10:30"}),
        context,
    )
    deleted = executor.execute(PlannedAction(ActionType.CALENDAR_DELETE, {"title": "созвон"}), context)

    assert "Календарь" in listed.message
    assert "Перенёс событие" in moved.message
    assert "Удалил событие" in deleted.message
    assert task_center.calls == [
        ("list_calendar_events", "today"),
        ("move_calendar_event", "созвон | start=tomorrow 10:00 | end=tomorrow 10:30"),
        ("delete_calendar_event", "созвон"),
    ]


def test_executor_routes_calendar_tomorrow_list_to_registered_tool() -> None:
    task_center = FakeTaskCenter()
    context = make_context(task_center=task_center)
    executor = ActionExecutor(build_default_tool_registry())

    result = executor.execute(PlannedAction(ActionType.CALENDAR_LIST, {"when": "tomorrow"}), context)

    assert "Календарь" in result.message
    assert task_center.calls == [("list_calendar_events", "tomorrow")]
