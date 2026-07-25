from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


class TaskCalendarError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class TaskCalendarHealth:
    trello_ok: bool
    trello_detail: str
    calendar_ok: bool
    calendar_detail: str

    @property
    def ok(self) -> bool:
        return self.trello_ok and self.calendar_ok


@dataclass(frozen=True)
class TaskCalendarAdapter:
    root: Path
    python_executable: str = ".venv/bin/python"
    timeout_seconds: float = 45
    health_cache_seconds: float = 30
    runner: Runner = subprocess.run
    _health_cache_value: TaskCalendarHealth | None = field(default=None, init=False, compare=False, repr=False)
    _health_cache_at: float = field(default=0, init=False, compare=False, repr=False)

    @classmethod
    def from_env(cls) -> "TaskCalendarAdapter":
        root = os.getenv("TASK_COMMAND_CENTER_DIR", "").strip()
        if not root:
            raise TaskCalendarError("TASK_COMMAND_CENTER_DIR не настроен.")
        return cls(
            root=Path(root).expanduser(),
            python_executable=os.getenv("TASK_COMMAND_CENTER_PYTHON", ".venv/bin/python"),
            timeout_seconds=float(os.getenv("TASK_COMMAND_CENTER_TIMEOUT_SECONDS", "45")),
            health_cache_seconds=float(os.getenv("TASK_COMMAND_CENTER_HEALTH_CACHE_SECONDS", "30")),
        )

    def execute_batch(self, actions: list[dict[str, object]]) -> list[dict[str, object]]:
        if not actions or len(actions) > 20:
            raise TaskCalendarError("Batch должен содержать от 1 до 20 действий.")
        allowed = {
            "task.create", "task.move", "task.priority", "task.done", "task.delete",
            "calendar.create", "calendar.move", "calendar.delete",
        }
        for item in actions:
            if str(item.get("type") or "") not in allowed or not isinstance(item.get("payload"), dict):
                raise TaskCalendarError("Batch содержит недопустимое действие.")
        output = self._run_python(_BATCH_SCRIPT, {"actions": actions}, max_output_chars=16_000)
        try:
            results = json.loads(output)
        except json.JSONDecodeError as error:
            raise TaskCalendarError("Task Command Center вернул некорректный batch JSON.") from error
        if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
            raise TaskCalendarError("Task Command Center вернул некорректный batch result.")
        return results

    def create_task(
        self,
        *,
        title: str,
        list_name: str = "Inbox",
        project: str | None = None,
        priority: str | None = None,
        due: str | None = None,
        description: str | None = None,
    ) -> str:
        args = [*self._base_args(), "new", "--title", _required(title, "title"), "--list", list_name]
        _append(args, "--project", project)
        _append(args, "--priority", priority)
        _append(args, "--due", due)
        _append(args, "--description", description)
        return self._run(args)

    def list_tasks(self, *, list_name: str | None = None) -> str:
        args = [*self._base_args(), "list"]
        _append(args, "--list", list_name)
        return self._run(args)

    def move_task(self, *, title: str, target_list: str) -> str:
        return self._run(
            [*self._base_args(), "move", "--card", _required(title, "title"), "--to", _required(target_list, "target list")]
        )

    def complete_task(self, *, title: str, summary: str = "Готово.") -> str:
        return self._run(
            [*self._base_args(), "done", "--card", _required(title, "title"), "--summary", _required(summary, "summary")]
        )

    def delete_task(self, *, title: str) -> str:
        return self._run([*self._base_args(), "delete", "--card", _required(title, "title"), "--yes"])

    def set_task_priority(self, *, title: str, priority: str) -> str:
        return self._run_python(
            _TASK_PRIORITY_SCRIPT,
            {"title": _required(title, "title"), "priority": _required(priority, "priority")},
        )

    def create_calendar_event(
        self,
        *,
        title: str,
        start: str,
        end: str,
        reminder_minutes: int | None = None,
        description: str | None = None,
    ) -> str:
        args = [
            *self._base_args(),
            "calendar",
            "--title",
            _required(title, "title"),
            "--start",
            _normalize_calendar_datetime(start),
            "--end",
            _normalize_calendar_datetime(end),
        ]
        if reminder_minutes is not None:
            args.extend(["--reminder", str(max(0, reminder_minutes))])
        _append(args, "--description", description)
        return self._run(args)

    def list_calendar_events(self, *, when: str = "today") -> str:
        return self._run_python(_CALENDAR_LIST_SCRIPT, {"when": when})

    def dashboard_tasks(self) -> dict[str, object]:
        return self._run_json(_TASK_DASHBOARD_SCRIPT, {})

    def dashboard_calendar(self, *, days: int = 7) -> dict[str, object]:
        return self._run_json(_CALENDAR_DASHBOARD_SCRIPT, {"days": max(1, min(int(days), 31))})

    def move_calendar_event(self, *, title: str, start: str, end: str) -> str:
        return self._run_python(
            _CALENDAR_MOVE_SCRIPT,
            {
                "title": _required(title, "title"),
                "start": _normalize_calendar_datetime(start),
                "end": _normalize_calendar_datetime(end),
            },
        )

    def delete_calendar_event(self, *, title: str) -> str:
        return self._run_python(_CALENDAR_DELETE_SCRIPT, {"title": _required(title, "title")})

    def health_check(self, *, force: bool = False) -> TaskCalendarHealth:
        now = time.monotonic()
        if (
            not force
            and self._health_cache_value is not None
            and now - self._health_cache_at < max(0, self.health_cache_seconds)
        ):
            return self._health_cache_value
        if not self.root.exists():
            detail = f"Task Command Center не найден: {self.root}"
            result = TaskCalendarHealth(False, detail, False, detail)
            object.__setattr__(self, "_health_cache_value", result)
            object.__setattr__(self, "_health_cache_at", now)
            return result
        trello_ok, trello_detail = self._probe([*self._base_args(), "list", "--list", "Today"])
        calendar_ok, calendar_detail = self._probe([str(self._python_path()), "-c", _CALENDAR_HEALTH_SCRIPT])
        result = TaskCalendarHealth(trello_ok, trello_detail, calendar_ok, calendar_detail)
        object.__setattr__(self, "_health_cache_value", result)
        object.__setattr__(self, "_health_cache_at", now)
        return result

    def _base_args(self) -> list[str]:
        return [str(self._python_path()), "taskctl.py"]

    def _python_path(self) -> Path:
        value = Path(self.python_executable)
        return value if value.is_absolute() else self.root / value

    def _run_python(
        self,
        script: str,
        payload: dict[str, object],
        *,
        max_output_chars: int = 3000,
    ) -> str:
        return self._run(
            [str(self._python_path()), "-c", script, json.dumps(payload, ensure_ascii=False)],
            max_output_chars=max_output_chars,
        )

    def _run_json(self, script: str, payload: dict[str, object]) -> dict[str, object]:
        output = self._run_python(script, payload, max_output_chars=32_000)
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as error:
            raise TaskCalendarError("Task Command Center вернул некорректный dashboard JSON.") from error
        if not isinstance(parsed, dict):
            raise TaskCalendarError("Task Command Center вернул некорректный dashboard JSON.")
        return parsed

    def _run(self, argv: list[str], *, max_output_chars: int = 3000) -> str:
        if not self.root.exists():
            raise TaskCalendarError(f"Task Command Center не найден: {self.root}")
        try:
            result = self.runner(
                argv,
                cwd=self.root,
                timeout=self.timeout_seconds,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except TypeError:
            result = self.runner(argv, cwd=self.root, timeout=self.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TaskCalendarError(f"Task Command Center недоступен: {type(error).__name__}") from error
        output = (result.stdout or "").strip()
        if result.returncode != 0:
            detail = (result.stderr or output or f"exit={result.returncode}").strip()
            raise TaskCalendarError(_bounded(detail, 500))
        return _bounded(output or "Готово.", max_output_chars)

    def _probe(self, argv: list[str]) -> tuple[bool, str]:
        try:
            return True, self._run(argv)
        except TaskCalendarError as error:
            return False, _bounded(str(error), 300)


def _required(value: str, label: str) -> str:
    clean = " ".join(value.split())
    if not clean:
        raise TaskCalendarError(f"{label} не должен быть пустым.")
    return clean


def _append(argv: list[str], flag: str, value: str | None) -> None:
    if value is not None and str(value).strip():
        argv.extend([flag, str(value).strip()])


def _normalize_calendar_datetime(value: str) -> str:
    clean = _required(value, "calendar datetime")
    candidate = clean[:-1] + "+00:00" if clean.endswith("Z") else clean
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return clean
    return parsed.strftime("%Y-%m-%d %H:%M")


def _bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


_SCRIPTS_DIR = Path(__file__).resolve().parent / "tcc_scripts"


def _load_script(name: str) -> str:
    return (_SCRIPTS_DIR / f"{name}.py").read_text(encoding="utf-8").strip()


_CALENDAR_HEALTH_SCRIPT = _load_script("calendar_health")
_CALENDAR_LIST_SCRIPT = _load_script("calendar_list")
_CALENDAR_MOVE_SCRIPT = _load_script("calendar_move")
_CALENDAR_DELETE_SCRIPT = _load_script("calendar_delete")
_TASK_DASHBOARD_SCRIPT = _load_script("task_dashboard")
_CALENDAR_DASHBOARD_SCRIPT = _load_script("calendar_dashboard")
_TASK_PRIORITY_SCRIPT = _load_script("task_priority")
_BATCH_SCRIPT = _load_script("batch")
