from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TaskCommandError(RuntimeError):
    pass


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        ...


def _default_runner(args: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        timeout=timeout,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@dataclass(frozen=True)
class TaskCommandCenter:
    root: Path
    python_executable: str = ".venv/bin/python"
    timeout_seconds: float = 40.0
    runner: CommandRunner = _default_runner

    def create_task(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        args = [
            *self._base_args(),
            "new",
            "--title",
            title,
            "--list",
            fields.get("list") or fields.get("список") or "Inbox",
        ]
        _append_optional(args, "--project", fields.get("project") or fields.get("проект"))
        _append_optional(args, "--priority", fields.get("priority") or fields.get("приоритет"))
        _append_optional(args, "--due", fields.get("due") or fields.get("дедлайн"))
        _append_optional(args, "--description", fields.get("description") or fields.get("описание"))
        for criteria in _split_many(fields.get("criteria") or fields.get("критерии")):
            args.extend(["--criteria", criteria])
        start = fields.get("start") or fields.get("начало")
        end = fields.get("end") or fields.get("конец")
        if start or end:
            if not start or not end:
                raise TaskCommandError("Для календарного блока нужны оба поля: start и end.")
            args.extend(["--calendar-start", start, "--calendar-end", end])
        _append_optional(args, "--reminder", fields.get("reminder") or fields.get("напоминание"))
        return self._run(args)

    def create_task_with_calendar(
        self,
        *,
        title: str,
        start: str | None = None,
        end: str | None = None,
        list_name: str = "Today",
        project: str | None = "Personal",
        priority: str | None = "P3",
        description: str = "Created from natural Telegram task batch.",
        reminder_minutes: int | None = 5,
    ) -> str:
        args = [
            *self._base_args(),
            "new",
            "--title",
            title,
            "--list",
            list_name,
            "--description",
            description,
        ]
        _append_optional(args, "--project", project)
        _append_optional(args, "--priority", priority)
        if start or end:
            if not start or not end:
                raise TaskCommandError("Для календарного блока нужны оба поля: start и end.")
            args.extend(["--calendar-start", start, "--calendar-end", end])
            if reminder_minutes is not None:
                args.extend(["--reminder", str(reminder_minutes)])
        return self._run(args)

    def list_tasks(self, text: str) -> str:
        fields = _parse_fields(text, allow_positional=False)
        args = [*self._base_args(), "list"]
        list_name = fields.get("list") or fields.get("список") or _positional_list_name(text)
        _append_optional(args, "--list", list_name)
        _append_optional(args, "--project", fields.get("project") or fields.get("проект"))
        _append_optional(args, "--priority", fields.get("priority") or fields.get("приоритет"))
        return self._run(args)

    def move_task(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        target = fields.get("to") or fields.get("в") or fields.get("list") or fields.get("список")
        if not target:
            raise TaskCommandError("Укажи список назначения: /task_move название | to=Today")
        return self._run([*self._base_args(), "move", "--card", title, "--to", target])

    def complete_task(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        summary = fields.get("summary") or fields.get("итог") or "Готово."
        return self._run([*self._base_args(), "done", "--card", title, "--summary", summary])

    def create_calendar_event(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        start = fields.get("start") or fields.get("начало")
        end = fields.get("end") or fields.get("конец")
        if not start or not end:
            raise TaskCommandError(
                "Формат: /calendar название | start=2026-07-10 10:00 | end=2026-07-10 10:30"
            )
        args = [*self._base_args(), "calendar", "--title", title, "--start", start, "--end", end]
        _append_optional(args, "--reminder", fields.get("reminder") or fields.get("напоминание"))
        _append_optional(args, "--description", fields.get("description") or fields.get("описание"))
        return self._run(args)

    def _base_args(self) -> list[str]:
        python_path = Path(self.python_executable)
        if not python_path.is_absolute():
            python_path = self.root / python_path
        return [str(python_path), "taskctl.py"]

    def _run(self, args: list[str]) -> str:
        if not self.root.exists():
            raise TaskCommandError(f"Task Command Center не найден: {self.root}")
        result = self.runner(args, cwd=self.root, timeout=self.timeout_seconds)
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        if result.returncode != 0:
            message = error or output or f"taskctl exited with {result.returncode}"
            raise TaskCommandError(_truncate(message, 900))
        return _truncate(output or "Готово.", 1800)


def _parse_fields(text: str, *, allow_positional: bool = True) -> dict[str, str]:
    chunks = [chunk.strip() for chunk in (text or "").split("|") if chunk.strip()]
    fields: dict[str, str] = {}
    if allow_positional and chunks and "=" not in chunks[0]:
        fields["title"] = chunks.pop(0)
    for chunk in chunks:
        if "=" not in chunk:
            if allow_positional and "title" not in fields:
                fields["title"] = chunk
            continue
        key, value = chunk.split("=", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def _required_title(fields: dict[str, str]) -> str:
    title = fields.get("title") or fields.get("название")
    if not title:
        raise TaskCommandError("Напиши название после команды.")
    return title


def _append_optional(args: list[str], flag: str, value: str | None) -> None:
    if value:
        args.extend([flag, value])


def _split_many(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(";") if item.strip()]


def _positional_list_name(text: str) -> str | None:
    value = (text or "").strip()
    return value or None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
