from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
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
    runner: Runner = subprocess.run

    @classmethod
    def from_env(cls) -> "TaskCalendarAdapter":
        root = os.getenv("TASK_COMMAND_CENTER_DIR", "").strip()
        if not root:
            raise TaskCalendarError("TASK_COMMAND_CENTER_DIR не настроен.")
        return cls(
            root=Path(root).expanduser(),
            python_executable=os.getenv("TASK_COMMAND_CENTER_PYTHON", ".venv/bin/python"),
            timeout_seconds=float(os.getenv("TASK_COMMAND_CENTER_TIMEOUT_SECONDS", "45")),
        )

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

    def health_check(self) -> TaskCalendarHealth:
        if not self.root.exists():
            detail = f"Task Command Center не найден: {self.root}"
            return TaskCalendarHealth(False, detail, False, detail)
        trello_ok, trello_detail = self._probe([*self._base_args(), "list", "--list", "Today"])
        calendar_ok, calendar_detail = self._probe([str(self._python_path()), "-c", _CALENDAR_HEALTH_SCRIPT])
        return TaskCalendarHealth(trello_ok, trello_detail, calendar_ok, calendar_detail)

    def _base_args(self) -> list[str]:
        return [str(self._python_path()), "taskctl.py"]

    def _python_path(self) -> Path:
        value = Path(self.python_executable)
        return value if value.is_absolute() else self.root / value

    def _run_python(self, script: str, payload: dict[str, str]) -> str:
        return self._run([str(self._python_path()), "-c", script, json.dumps(payload, ensure_ascii=False)])

    def _run(self, argv: list[str]) -> str:
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
        return _bounded(output or "Готово.", 3000)

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


_CALENDAR_HEALTH_SCRIPT = """
from pathlib import Path
from src.config import load_config
from src.google_calendar_client import GoogleCalendarClient
config = load_config(Path('.'))
calendar = GoogleCalendarClient(config, Path('.'))
calendar.validate_setup()
print(f'calendar_ok events_today={len(calendar.list_today_events())}')
""".strip()


_CALENDAR_LIST_SCRIPT = """
import json, sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from src.config import load_config
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient, _event_start
payload = json.loads(sys.argv[1])
config = load_config(Path('.'))
calendar = GoogleCalendarClient(config, Path('.'))
when = str(payload.get('when') or 'today').strip().lower()
tz = ZoneInfo(config.timezone)
day = date.today() + (timedelta(days=1) if when in {'tomorrow', 'завтра'} else timedelta())
start = datetime.combine(day, time.min, tz)
end = start + timedelta(days=1)
if config.mock:
    events = [event for event in calendar._load_store()['events'] if _event_start(event) and start <= _event_start(event) < end]
else:
    events = list(calendar.authenticate().events().list(calendarId=config.google_calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy='startTime').execute().get('items', []))
print('No events found.' if not events else '\\n'.join(format_event(event) for event in events))
""".strip()


_CALENDAR_MOVE_SCRIPT = """
import json, sys
from pathlib import Path
from src.config import load_config
from src.date_parser import parse_datetime
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient
from src.models import CalendarEventInput
payload = json.loads(sys.argv[1]); config = load_config(Path('.')); calendar = GoogleCalendarClient(config, Path('.'))
event = calendar.find_event_by_title(payload['title'])
body = calendar.build_event_body(CalendarEventInput(title=str(event.get('summary') or payload['title']), start=parse_datetime(payload['start'], config.timezone), end=parse_datetime(payload['end'], config.timezone), reminder_minutes=None, description=str(event.get('description') or '')))
event_id = str(event['id'])
if config.mock:
    store = calendar._load_store(); moved = next((item for item in store['events'] if str(item.get('id')) == event_id), None)
    if moved is None: raise SystemExit('Calendar event not found.')
    moved.update(body); moved['id'] = event_id; moved['htmlLink'] = event.get('htmlLink'); calendar._save_store(store)
else:
    moved = calendar.authenticate().events().update(calendarId=config.google_calendar_id, eventId=event_id, body=body).execute()
print(format_event(moved)); print(f"calendar_event_id={moved.get('id')}")
if moved.get('htmlLink'): print(moved.get('htmlLink'))
""".strip()


_CALENDAR_DELETE_SCRIPT = """
import json, sys
from pathlib import Path
from src.config import load_config
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient
payload = json.loads(sys.argv[1]); config = load_config(Path('.')); calendar = GoogleCalendarClient(config, Path('.'))
event = calendar.delete_event_by_title(payload['title'])
print(format_event(event)); print(f"calendar_event_id={event.get('id')}")
if event.get('htmlLink'): print(event.get('htmlLink'))
""".strip()
