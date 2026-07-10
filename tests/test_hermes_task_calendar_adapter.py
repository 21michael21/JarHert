from __future__ import annotations

import subprocess

import pytest

from hermes.native_tools.task_calendar import TaskCalendarAdapter, TaskCalendarError


def make_adapter(tmp_path, calls):
    root = tmp_path / "task-command-center"
    root.mkdir()

    def runner(argv, *, cwd, timeout):
        calls.append((argv, cwd, timeout))
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    return TaskCalendarAdapter(root=root, runner=runner)


def test_task_crud_uses_structured_argv_without_shell(tmp_path) -> None:
    calls = []
    adapter = make_adapter(tmp_path, calls)

    adapter.create_task(title="Проверить релиз", list_name="Today", project="Hub_ML")
    adapter.list_tasks(list_name="Today")
    adapter.move_task(title="Проверить релиз", target_list="Done")
    adapter.complete_task(title="Проверить релиз", summary="Проверено")
    adapter.delete_task(title="Проверить релиз")

    commands = [call[0] for call in calls]
    assert commands[0][-7:] == [
        "new", "--title", "Проверить релиз", "--list", "Today", "--project", "Hub_ML",
    ]
    assert commands[1][-3:] == ["list", "--list", "Today"]
    assert commands[2][-5:] == ["move", "--card", "Проверить релиз", "--to", "Done"]
    assert commands[3][-5:] == ["done", "--card", "Проверить релиз", "--summary", "Проверено"]
    assert commands[4][-4:] == ["delete", "--card", "Проверить релиз", "--yes"]
    assert all(call[1] == adapter.root for call in calls)


def test_calendar_crud_uses_adapter_scripts(tmp_path) -> None:
    calls = []
    adapter = make_adapter(tmp_path, calls)

    adapter.create_calendar_event(
        title="JarHert canary",
        start="2030-01-02 12:00",
        end="2030-01-02 12:15",
        reminder_minutes=5,
    )
    adapter.list_calendar_events(when="tomorrow")
    adapter.move_calendar_event(
        title="JarHert canary",
        start="2030-01-02 13:00",
        end="2030-01-02 13:15",
    )
    adapter.delete_calendar_event(title="JarHert canary")

    assert calls[0][0][-9:] == [
        "calendar", "--title", "JarHert canary", "--start", "2030-01-02 12:00",
        "--end", "2030-01-02 12:15", "--reminder", "5",
    ]
    assert all("-c" in call[0] for call in calls[1:])


def test_health_checks_both_integrations(tmp_path) -> None:
    calls = []
    adapter = make_adapter(tmp_path, calls)

    health = adapter.health_check()

    assert health.ok is True
    assert health.trello_ok is True
    assert health.calendar_ok is True
    assert len(calls) == 2


def test_failed_command_returns_bounded_friendly_error(tmp_path) -> None:
    root = tmp_path / "tcc"
    root.mkdir()

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="provider failed " * 100)

    adapter = TaskCalendarAdapter(root=root, runner=runner)

    with pytest.raises(TaskCalendarError) as raised:
        adapter.list_tasks()

    assert "provider failed" in str(raised.value)
    assert len(str(raised.value)) <= 504


def test_missing_integration_path_fails_without_local_default(tmp_path) -> None:
    adapter = TaskCalendarAdapter(root=tmp_path / "missing")

    with pytest.raises(TaskCalendarError, match="не найден"):
        adapter.list_tasks()
