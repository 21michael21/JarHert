from __future__ import annotations

import importlib


def test_task_command_center_dir_has_no_local_default(monkeypatch) -> None:
    monkeypatch.setenv("TASK_COMMAND_CENTER_DIR", "")
    import backend.config as config

    reloaded = importlib.reload(config)

    assert reloaded.Settings().task_command_center_dir == ""


def test_task_command_center_dir_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("TASK_COMMAND_CENTER_DIR", "/opt/task-command-center")
    import backend.config as config

    reloaded = importlib.reload(config)

    assert reloaded.Settings().task_command_center_dir == "/opt/task-command-center"


def test_gateway_does_not_build_task_center_without_dir(monkeypatch) -> None:
    monkeypatch.setenv("TASK_COMMAND_CENTER_ENABLED", "true")
    monkeypatch.setenv("TASK_COMMAND_CENTER_DIR", "")
    import backend.config as config
    import gateway_bot.main as main

    importlib.reload(config)
    reloaded = importlib.reload(main)

    assert reloaded.build_task_center() is None
