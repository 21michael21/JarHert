from __future__ import annotations

import subprocess
from pathlib import Path

from assistant.task_command_center import TaskCommandCenter, TaskCommandError


def fake_runner(calls):
    def run(args, *, cwd: Path, timeout: float):
        calls.append((args, cwd, timeout))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    return run


def test_create_task_builds_taskctl_args(tmp_path) -> None:
    calls = []
    center = TaskCommandCenter(root=tmp_path, runner=fake_runner(calls))

    output = center.create_task(
        "Проверить Trello | list=Today | project=Personal | priority=P2 | due=2026-07-10"
    )

    assert output == "ok"
    args = calls[0][0]
    assert args[:2] == [str(tmp_path / ".venv/bin/python"), "taskctl.py"]
    assert args[2:] == [
        "new",
        "--title",
        "Проверить Trello",
        "--list",
        "Today",
        "--project",
        "Personal",
        "--priority",
        "P2",
        "--due",
        "2026-07-10",
    ]


def test_calendar_requires_start_and_end(tmp_path) -> None:
    center = TaskCommandCenter(root=tmp_path, runner=fake_runner([]))

    try:
        center.create_calendar_event("Созвон | start=2026-07-10 10:00")
    except TaskCommandError as exc:
        assert "start" in str(exc)
    else:
        raise AssertionError("expected TaskCommandError")


def test_list_tasks_uses_positional_list_name(tmp_path) -> None:
    calls = []
    center = TaskCommandCenter(root=tmp_path, runner=fake_runner(calls))

    center.list_tasks("Today")

    assert calls[0][0][2:] == ["list", "--list", "Today"]
