from __future__ import annotations

from dataclasses import replace

from backend.config import Settings
from scripts.preflight import _validate_task_center_config


def _settings(**updates) -> Settings:
    values = {"task_command_center_enabled": True, "task_command_center_dir": ""}
    values.update(updates)
    return replace(Settings(), **values)


def test_preflight_rejects_empty_task_command_center_dir() -> None:
    errors = _validate_task_center_config(_settings(task_command_center_dir=""))

    assert errors == ["TASK_COMMAND_CENTER_ENABLED=true, but TASK_COMMAND_CENTER_DIR is empty"]


def test_preflight_rejects_missing_task_command_center_dir(tmp_path) -> None:
    missing = tmp_path / "missing"

    errors = _validate_task_center_config(_settings(task_command_center_dir=str(missing)))

    assert errors == [f"TASK_COMMAND_CENTER_ENABLED=true, but TASK_COMMAND_CENTER_DIR does not exist: {missing}"]


def test_preflight_accepts_existing_task_command_center_dir(tmp_path) -> None:
    errors = _validate_task_center_config(_settings(task_command_center_dir=str(tmp_path)))

    assert errors == []
