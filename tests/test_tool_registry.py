from __future__ import annotations

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
        return "task created"

    def create_task_with_calendar(self, **kwargs):
        self.calls.append(("create_task_with_calendar", kwargs))
        return "task and calendar created"

    def list_tasks(self, text):
        self.calls.append(("list_tasks", text))
        return "task list"

    def move_task(self, text):
        self.calls.append(("move_task", text))
        return "task moved"

    def complete_task(self, text):
        self.calls.append(("complete_task", text))
        return "task done"

    def create_calendar_event(self, text):
        self.calls.append(("create_calendar_event", text))
        return "calendar event"


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
        "calendar.create",
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
