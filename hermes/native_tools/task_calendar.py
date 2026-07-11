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
            "task.create", "task.move", "task.done", "task.delete",
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


_BATCH_SCRIPT = """
import json, sys
from pathlib import Path
from src.config import load_config
from src.date_parser import parse_date, parse_datetime
from src.google_calendar_client import GoogleCalendarClient
from src.models import CalendarEventInput, TaskCardInput
from src.trello_client import TrelloClient

payload = json.loads(sys.argv[1])
config = load_config(Path('.'))
trello = None
calendar = None

def get_trello():
    global trello
    if trello is None:
        trello = TrelloClient(config, Path('.'))
    return trello

def get_calendar():
    global calendar
    if calendar is None:
        calendar = GoogleCalendarClient(config, Path('.'))
        if not config.mock:
            service = calendar.authenticate()
            calendar.authenticate = lambda: service
    return calendar

def task_result(card):
    parts = [f"trello_card_id={card.get('id')}"]
    if card.get('shortUrl') or card.get('url'):
        parts.append(str(card.get('shortUrl') or card.get('url')))
    return "\\n".join(parts)

def calendar_result(event):
    parts = [f"calendar_event_id={event.get('id')}"]
    if event.get('htmlLink'):
        parts.append(str(event.get('htmlLink')))
    return "\\n".join(parts)

results = []
for action in payload['actions']:
    kind = action['type']; data = action['payload']
    try:
        if kind == 'task.create':
            card = get_trello().create_card(TaskCardInput(
                title=data['title'], project=data.get('project'), priority=data.get('priority'),
                list_name=data.get('list_name') or 'Inbox', due=parse_date(data.get('due')),
                description=data.get('description') or '', criteria=[],
            ))
            result = task_result(card)
        elif kind == 'task.move':
            client = get_trello(); result = task_result(client.move_card(client.find_card_by_name(data['title']), data['target_list']))
        elif kind == 'task.done':
            client = get_trello(); card = client.find_card_by_name(data['title'])
            client.add_comment(card, f"Done summary: {data.get('summary') or 'Готово.'}")
            result = task_result(client.move_card(card, 'Done'))
        elif kind == 'task.delete':
            client = get_trello(); card = client.find_card_by_name(data['title']); client.delete_card(card)
            result = task_result(card)
        elif kind == 'calendar.create':
            event = get_calendar().create_event(CalendarEventInput(
                title=data['title'], start=parse_datetime(data['start'], config.timezone),
                end=parse_datetime(data['end'], config.timezone), reminder_minutes=data.get('reminder_minutes'),
                description=data.get('description') or '',
            ))
            result = calendar_result(event)
        elif kind == 'calendar.move':
            client = get_calendar(); event = client.find_event_by_title(data['title']); event_id = str(event['id'])
            body = client.build_event_body(CalendarEventInput(
                title=str(event.get('summary') or data['title']), start=parse_datetime(data['start'], config.timezone),
                end=parse_datetime(data['end'], config.timezone), reminder_minutes=None,
                description=str(event.get('description') or ''),
            ))
            if config.mock:
                store = client._load_store(); moved = next(item for item in store['events'] if str(item.get('id')) == event_id)
                moved.update(body); moved['id'] = event_id; moved['htmlLink'] = event.get('htmlLink'); client._save_store(store)
            else:
                moved = client.authenticate().events().update(calendarId=config.google_calendar_id, eventId=event_id, body=body).execute()
            result = calendar_result(moved)
        elif kind == 'calendar.delete':
            result = calendar_result(get_calendar().delete_event_by_title(data['title']))
        else:
            raise ValueError(f"Unsupported action: {kind}")
        results.append({'ok': True, 'result': result})
    except Exception as error:
        results.append({'ok': False, 'error': str(error)[:500] or type(error).__name__})
print(json.dumps(results, ensure_ascii=False, separators=(',', ':')))
""".strip()
