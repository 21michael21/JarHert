from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from assistant.action_schema import ActionType, PlannedAction
from assistant.agent_jobs import build_agent_plan
from assistant.preferences import UserPreferences
from assistant.quality_gates import check_input
from assistant.task_command_center import TaskCommandCenter, TaskCommandError
from assistant.tool_result_ids import extract_tool_result_ids
from assistant.types import UserContext
from reminders.parser import parse_reminder


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class InputSchema:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    max_chars: int = 2000


@dataclass(frozen=True)
class ToolExecutionResult:
    message: str
    meta: dict[str, str] = field(default_factory=dict)


class ToolExecutionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, kind: str = "permanent") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind


@dataclass(frozen=True)
class ToolContext:
    user: UserContext
    memories: object
    ideas: object
    reminders: object
    docs_sync: object
    task_center: TaskCommandCenter | None = None
    agent_jobs: object | None = None
    knowledge: object | None = None
    contact_book: object | None = None
    delivery_outbox: object | None = None
    telegram_reply: Callable[[int, str], None] | None = None
    preferences: UserPreferences | None = None
    idempotency_key: str = ""


ToolHandler = Callable[[dict[str, str], ToolContext], ToolExecutionResult]


@dataclass(frozen=True)
class ToolSpec:
    name: ActionType
    input_schema: InputSchema
    timeout_seconds: float
    risk: ToolRisk
    handler: ToolHandler
    retryable_errors: tuple[str, ...] = ()
    permanent_errors: tuple[str, ...] = ("validation", "not_configured", "unsupported")

    def classify_error(self, error: Exception) -> ToolExecutionError:
        if isinstance(error, ToolExecutionError):
            return error
        message = str(error)
        lowered = message.lower()
        retryable = any(marker in lowered for marker in self.retryable_errors)
        kind = "retryable" if retryable else "permanent"
        return ToolExecutionError(message, retryable=retryable, kind=kind)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[ActionType, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: ActionType) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError(f"Неподдерживаемое действие: {name.value}", kind="permanent") from exc

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def execute(self, action: PlannedAction, context: ToolContext) -> ToolExecutionResult:
        spec = self.get(action.type)
        _validate_payload(action.payload, spec.input_schema)
        try:
            result = spec.handler(action.payload, context)
            if not context.idempotency_key:
                return result
            return ToolExecutionResult(
                result.message,
                meta={**result.meta, "idempotency_key": context.idempotency_key},
            )
        except Exception as exc:
            raise spec.classify_error(exc) from exc


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in _default_tool_specs():
        registry.register(spec)
    return registry


def _default_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=ActionType.IDEA_SAVE,
            input_schema=InputSchema(required=("text",), max_chars=1000),
            timeout_seconds=3,
            risk=ToolRisk.LOW,
            handler=_idea_save,
            permanent_errors=("validation",),
        ),
        ToolSpec(
            name=ActionType.MEMORY_SAVE,
            input_schema=InputSchema(required=("text",), max_chars=1000),
            timeout_seconds=3,
            risk=ToolRisk.LOW,
            handler=_memory_save,
            permanent_errors=("validation",),
        ),
        ToolSpec(
            name=ActionType.REMINDER_CREATE,
            input_schema=InputSchema(required=("text",), max_chars=1000),
            timeout_seconds=3,
            risk=ToolRisk.LOW,
            handler=_reminder_create,
            permanent_errors=("validation", "parse_failed"),
        ),
        ToolSpec(
            name=ActionType.TASK_CREATE,
            input_schema=InputSchema(required=("title",), optional=("start", "end", "list", "project")),
            timeout_seconds=20,
            risk=ToolRisk.MEDIUM,
            handler=_task_create,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("validation", "not_configured"),
        ),
        ToolSpec(
            name=ActionType.TASK_LIST,
            input_schema=InputSchema(optional=("list",)),
            timeout_seconds=15,
            risk=ToolRisk.LOW,
            handler=_task_list,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("not_configured",),
        ),
        ToolSpec(
            name=ActionType.TASK_MOVE,
            input_schema=InputSchema(required=("title", "to")),
            timeout_seconds=20,
            risk=ToolRisk.MEDIUM,
            handler=_task_move,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("validation", "not_configured"),
        ),
        ToolSpec(
            name=ActionType.TASK_DONE,
            input_schema=InputSchema(required=("title",)),
            timeout_seconds=20,
            risk=ToolRisk.MEDIUM,
            handler=_task_done,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("validation", "not_configured"),
        ),
        ToolSpec(
            name=ActionType.CALENDAR_CREATE,
            input_schema=InputSchema(required=("title", "start", "end")),
            timeout_seconds=20,
            risk=ToolRisk.MEDIUM,
            handler=_calendar_create,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("validation", "not_configured"),
        ),
        ToolSpec(
            name=ActionType.TELEGRAM_REPLY,
            input_schema=InputSchema(required=("text",), max_chars=3500),
            timeout_seconds=5,
            risk=ToolRisk.LOW,
            handler=_telegram_reply,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("validation", "not_configured"),
        ),
        ToolSpec(
            name=ActionType.TELEGRAM_SEND_MESSAGE,
            input_schema=InputSchema(required=("recipient", "text"), optional=("send_at",), max_chars=3500),
            timeout_seconds=5,
            risk=ToolRisk.MEDIUM,
            handler=_telegram_send_message,
            retryable_errors=("timeout", "tempor", "network", "rate"),
            permanent_errors=("validation", "not_configured", "not_found"),
        ),
        ToolSpec(
            name=ActionType.AGENT_JOB_CREATE,
            input_schema=InputSchema(required=("goal",), max_chars=1500),
            timeout_seconds=3,
            risk=ToolRisk.LOW,
            handler=_agent_job_create,
            permanent_errors=("validation", "not_configured"),
        ),
    ]


def _validate_payload(payload: dict[str, str], schema: InputSchema) -> None:
    allowed = set(schema.required) | set(schema.optional)
    for field_name in schema.required:
        if not str(payload.get(field_name) or "").strip():
            raise ToolExecutionError(f"Не хватает поля {field_name}.", kind="permanent")
    for key, value in payload.items():
        if key not in allowed:
            continue
        if not isinstance(value, str):
            raise ToolExecutionError(f"Поле {key} должно быть строкой.", kind="permanent")
        if not check_input(value, max_chars=schema.max_chars).ok:
            raise ToolExecutionError(f"Поле {key} не прошло проверку безопасности.", kind="permanent")


def _idea_save(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    if context.knowledge is not None and hasattr(context.knowledge, "create"):
        item = context.knowledge.create(
            user_id=context.user.user_id,
            text=payload["text"],
            note_type="idea",
            source="telegram",
        )
        if not _store_uses_knowledge(context.ideas, context.knowledge):
            context.ideas.add(context.user.user_id, payload["text"])
    else:
        item = context.ideas.add(context.user.user_id, payload["text"])
    synced = context.docs_sync.append(
        kind="idea",
        user_id=context.user.user_id,
        text=item.text,
        created_at=item.created_at,
        record_id=str(item.id),
    )
    suffix = " Отправил в Google Docs." if synced else ""
    return ToolExecutionResult(f"Сохранил идею #{item.id}.{suffix}")


def _memory_save(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    if context.knowledge is not None and hasattr(context.knowledge, "create"):
        item = context.knowledge.create(
            user_id=context.user.user_id,
            text=payload["text"],
            note_type="memory",
            source="telegram",
        )
        if not _store_uses_knowledge(context.memories, context.knowledge):
            context.memories.add(context.user.user_id, payload["text"])
    else:
        item = context.memories.add(context.user.user_id, payload["text"])
    return ToolExecutionResult(f"Сохранил важное #{item.id}.")


def _store_uses_knowledge(store: object, knowledge: object) -> bool:
    store_knowledge = getattr(store, "knowledge", None)
    if store_knowledge is None:
        return False
    if store_knowledge is knowledge:
        return True
    return getattr(store_knowledge, "session_factory", None) is getattr(knowledge, "session_factory", None)


def _reminder_create(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    default_time = context.preferences.default_reminder_time if context.preferences else "09:00"
    parsed = parse_reminder(payload["text"], default_time=default_time)
    if parsed is None:
        raise ToolExecutionError("Не понял время напоминания.", kind="permanent")
    item = context.reminders.add(context.user.user_id, parsed.text, parsed.remind_at)
    synced = context.docs_sync.append(
        kind="reminder",
        user_id=context.user.user_id,
        text=f"{item.remind_at.isoformat()} — {item.text}",
        created_at=item.remind_at,
        record_id=str(item.id),
    )
    suffix = " Отправил в Google Docs." if synced else ""
    return ToolExecutionResult(
        f"Поставил напоминание #{item.id}: {item.remind_at.isoformat()} — {item.text}{suffix}"
    )


def _task_create(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    center = _require_task_center(context)
    title = payload["title"]
    start = payload.get("start")
    end = payload.get("end")
    list_name = payload.get("list")
    project = payload.get("project")
    if start and end:
        kwargs = {"title": title, "start": start, "end": end}
        if list_name:
            kwargs["list_name"] = list_name
        if project:
            kwargs["project"] = project
        output = center.create_task_with_calendar(**kwargs)
        return ToolExecutionResult(f"Создал задачу «{title}» на {start}.", meta=extract_tool_result_ids(output))
    command = title
    if list_name:
        command += f" | list={list_name}"
    if project:
        command += f" | project={project}"
    output = center.create_task(command)
    return ToolExecutionResult(f"Создал задачу «{title}».", meta=extract_tool_result_ids(output))


def _task_list(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    center = _require_task_center(context)
    output = center.list_tasks(payload.get("list", ""))
    return ToolExecutionResult("Задачи:\n" + output)


def _task_move(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    center = _require_task_center(context)
    output = center.move_task(f"{payload['title']} | to={payload['to']}")
    return ToolExecutionResult("Переместил задачу:\n" + output, meta=extract_tool_result_ids(output))


def _task_done(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    center = _require_task_center(context)
    output = center.complete_task(payload["title"])
    return ToolExecutionResult("Закрыл задачу:\n" + output, meta=extract_tool_result_ids(output))


def _calendar_create(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    center = _require_task_center(context)
    output = center.create_calendar_event(
        f"{payload['title']} | start={payload['start']} | end={payload['end']}"
    )
    return ToolExecutionResult("Создал событие:\n" + output, meta=extract_tool_result_ids(output))


def _telegram_reply(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    if context.telegram_reply is None:
        raise ToolExecutionError("Telegram reply tool не подключён.", kind="permanent")
    context.telegram_reply(context.user.tg_user_id, payload["text"])
    return ToolExecutionResult("Отправил ответ в Telegram.")


def _telegram_send_message(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    if context.contact_book is None:
        raise ToolExecutionError("Contact book не подключён.", kind="not_configured")
    if context.delivery_outbox is None:
        raise ToolExecutionError("Delivery outbox не подключён.", kind="not_configured")
    contact = context.contact_book.resolve(context.user.user_id, payload["recipient"])
    if contact is None:
        raise ToolExecutionError(f"Не нашёл контакт: {payload['recipient']}", kind="not_found")
    chat_id = contact.chat_id or contact.tg_user_id
    if chat_id is None:
        raise ToolExecutionError(f"У контакта {contact.name} нет Telegram chat_id/tg_user_id.", kind="validation")
    message = context.delivery_outbox.enqueue(
        user_id=context.user.user_id,
        chat_id=int(chat_id),
        text=payload["text"],
        next_attempt_at=_parse_send_at(payload.get("send_at")),
        idempotency_key=(f"{context.idempotency_key}:delivery" if context.idempotency_key else None),
    )
    when = f" на {payload['send_at']}" if payload.get("send_at") else ""
    return ToolExecutionResult(
        f"Поставил отправку {contact.name}{when}.",
        meta={
            "delivery_id": str(message.id),
            "contact_id": str(contact.id),
            "telegram_chat_id": str(chat_id),
        },
    )


def _agent_job_create(payload: dict[str, str], context: ToolContext) -> ToolExecutionResult:
    if context.agent_jobs is None:
        raise ToolExecutionError("Agent job store не подключён.", kind="permanent")
    steps = build_agent_plan(payload["goal"])
    if not steps:
        raise ToolExecutionError("Цель агентской задачи пустая.", kind="permanent")
    job = context.agent_jobs.create(
        context.user.user_id,
        payload["goal"],
        steps,
        idempotency_key=(
            f"{context.idempotency_key}:job" if context.idempotency_key else None
        ),
    )
    lines = [
        f"Поставил в очередь job #{job.id}.",
        f"Статус: {job.status}",
        "План:",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(job.steps, start=1))
    lines.append(f"Проверить: /job {job.id}")
    return ToolExecutionResult("\n".join(lines))


def _require_task_center(context: ToolContext) -> TaskCommandCenter:
    if context.task_center is None:
        raise ToolExecutionError("Task Command Center не подключён.", kind="permanent")
    return context.task_center


def _parse_send_at(value: str | None) -> datetime | None:
    clean = (value or "").strip().lower()
    if not clean:
        return None
    now = datetime.now(timezone.utc)
    if clean == "сегодня":
        return now
    if clean == "завтра":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    relative_minutes = re.match(r"^через\s+(?P<num>\d+)\s+минут[уы]?$", clean)
    if relative_minutes:
        return now + timedelta(minutes=int(relative_minutes.group("num")))
    relative_hours = re.match(r"^через\s+(?P<num>\d+)\s+час(?:а|ов)?$", clean)
    if relative_hours:
        return now + timedelta(hours=int(relative_hours.group("num")))
    return None
