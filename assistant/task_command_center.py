from __future__ import annotations

import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TaskCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskCenterHealth:
    trello_ok: bool
    trello_detail: str
    calendar_ok: bool
    calendar_detail: str

    @property
    def ok(self) -> bool:
        return self.trello_ok and self.calendar_ok


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

    def delete_task(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        return self._run([*self._base_args(), "delete", "--card", title, "--yes"])

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

    def list_calendar_events(self, text: str) -> str:
        fields = _parse_fields(text, allow_positional=False)
        when = (fields.get("when") or fields.get("когда") or _positional_list_name(text) or "today").strip()
        return self._run_python(_CALENDAR_LIST_SCRIPT, {"when": when})

    def move_calendar_event(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        start = fields.get("start") or fields.get("начало")
        end = fields.get("end") or fields.get("конец")
        if not start or not end:
            raise TaskCommandError("Для переноса события нужны поля start и end.")
        return self._run_python(_CALENDAR_MOVE_SCRIPT, {"title": title, "start": start, "end": end})

    def delete_calendar_event(self, text: str) -> str:
        fields = _parse_fields(text)
        title = _required_title(fields)
        return self._run_python(_CALENDAR_DELETE_SCRIPT, {"title": title})

    def health_check(self) -> TaskCenterHealth:
        if not self.root.exists():
            detail = f"Task Command Center не найден: {self.root}"
            return TaskCenterHealth(
                trello_ok=False,
                trello_detail=detail,
                calendar_ok=False,
                calendar_detail=detail,
            )
        trello_ok, trello_detail = self._probe([*self._base_args(), "list", "--list", "Today"])
        calendar_ok, calendar_detail = self._probe([str(self._python_path()), "-c", _CALENDAR_HEALTH_PROBE])
        return TaskCenterHealth(
            trello_ok=trello_ok,
            trello_detail=trello_detail,
            calendar_ok=calendar_ok,
            calendar_detail=calendar_detail,
        )

    def _base_args(self) -> list[str]:
        return [str(self._python_path()), "taskctl.py"]

    def _python_path(self) -> Path:
        python_path = Path(self.python_executable)
        if not python_path.is_absolute():
            python_path = self.root / python_path
        return python_path

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

    def _run_python(self, script: str, payload: dict[str, str]) -> str:
        return self._run([str(self._python_path()), "-c", script, json.dumps(payload, ensure_ascii=False)])

    def _probe(self, args: list[str]) -> tuple[bool, str]:
        try:
            result = self.runner(args, cwd=self.root, timeout=self.timeout_seconds)
        except Exception as exc:
            return False, _truncate(f"{type(exc).__name__}: {exc}", 300)
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        detail = (output or error) if result.returncode == 0 else (error or output)
        detail = detail or f"exit={result.returncode}"
        return result.returncode == 0, _truncate(detail, 300)


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


_CALENDAR_HEALTH_PROBE = """
from pathlib import Path
from src.config import load_config
from src.google_calendar_client import GoogleCalendarClient

config = load_config(Path("."))
calendar = GoogleCalendarClient(config, Path("."))
calendar.validate_setup()
events = calendar.list_today_events()
print(f"calendar_ok events_today={len(events)}")
""".strip()


_CALENDAR_LIST_SCRIPT = """
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from src.config import load_config
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient, _event_start

payload = json.loads(sys.argv[1])
config = load_config(Path("."))
calendar = GoogleCalendarClient(config, Path("."))
when = str(payload.get("when") or "today").strip().lower()
tz = ZoneInfo(config.timezone)
day = date.today() + (timedelta(days=1) if when in {"tomorrow", "завтра"} else timedelta(days=0))
start = datetime.combine(day, time.min, tz)
end = start + timedelta(days=1)
if config.mock:
    events = [
        event
        for event in calendar._load_store()["events"]
        if _event_start(event) and start <= _event_start(event) < end
    ]
else:
    service = calendar.authenticate()
    response = service.events().list(
        calendarId=config.google_calendar_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = list(response.get("items", []))
print("No events found." if not events else "\\n".join(format_event(event) for event in events))
""".strip()


_CALENDAR_MOVE_SCRIPT = """
import json
import sys
from pathlib import Path
from src.config import load_config
from src.date_parser import parse_datetime
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient
from src.models import CalendarEventInput

payload = json.loads(sys.argv[1])
config = load_config(Path("."))
calendar = GoogleCalendarClient(config, Path("."))
event = calendar.find_event_by_title(payload["title"])
body = calendar.build_event_body(
    CalendarEventInput(
        title=str(event.get("summary") or payload["title"]),
        start=parse_datetime(payload["start"], config.timezone),
        end=parse_datetime(payload["end"], config.timezone),
        reminder_minutes=None,
        description=str(event.get("description") or ""),
    )
)
event_id = str(event["id"])
if config.mock:
    store = calendar._load_store()
    moved = None
    for item in store["events"]:
        if str(item.get("id")) == event_id:
            item.update(body)
            item["id"] = event_id
            item["htmlLink"] = event.get("htmlLink")
            moved = item
            break
    if moved is None:
        raise SystemExit(f"Cannot find calendar event id '{event_id}'.")
    calendar._save_store(store)
else:
    service = calendar.authenticate()
    moved = service.events().update(
        calendarId=config.google_calendar_id,
        eventId=event_id,
        body=body,
    ).execute()
print(format_event(moved))
print(f"calendar_event_id={moved.get('id')}")
if moved.get("htmlLink"):
    print(moved.get("htmlLink"))
""".strip()


_CALENDAR_DELETE_SCRIPT = """
import json
import sys
from pathlib import Path
from src.config import load_config
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient

payload = json.loads(sys.argv[1])
config = load_config(Path("."))
calendar = GoogleCalendarClient(config, Path("."))
event = calendar.delete_event_by_title(payload["title"])
print(format_event(event))
print(f"calendar_event_id={event.get('id')}")
if event.get("htmlLink"):
    print(event.get("htmlLink"))
""".strip()
