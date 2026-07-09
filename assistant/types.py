from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Intent(str, Enum):
    ASK = "ask"
    REMEMBER = "remember"
    MEMORIES = "memories"
    IDEA = "idea"
    IDEAS = "ideas"
    NOTE_CREATE = "note_create"
    NOTE_SEARCH = "note_search"
    NOTE_EDIT = "note_edit"
    NOTE_DELETE = "note_delete"
    NOTES = "notes"
    REMIND = "remind"
    REMINDERS = "reminders"
    CANCEL_REMINDER = "cancel_reminder"
    TASK = "task"
    TASKS = "tasks"
    TASK_DONE = "task_done"
    TASK_MOVE = "task_move"
    TASK_BATCH = "task_batch"
    CALENDAR = "calendar"
    AGENT_DO = "agent_do"
    AGENT_JOBS = "agent_jobs"
    AGENT_JOB = "agent_job"
    MONITOR_ADD = "monitor_add"
    MONITOR_LIST = "monitor_list"
    MONITOR_REMOVE = "monitor_remove"
    STATUS = "status"
    ADMIN_STATUS = "admin_status"
    TRACE = "trace"
    HELP = "help"
    UNKNOWN = "unknown"


class GateStatus(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"
    NEEDS_FALLBACK = "needs_fallback"


@dataclass(frozen=True)
class ReplyButton:
    text: str
    callback_data: str


@dataclass(frozen=True)
class UserContext:
    user_id: int
    tg_user_id: int
    timezone: str = "UTC"
    is_admin: bool = False


@dataclass(frozen=True)
class ParsedMessage:
    intent: Intent
    text: str
    raw_text: str


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    reason: str = ""
    safe_text: str = ""

    @property
    def ok(self) -> bool:
        return self.status == GateStatus.OK


@dataclass(frozen=True)
class HermesRequest:
    user: UserContext
    prompt: str
    intent: Intent = Intent.ASK
    context: dict[str, str] = field(default_factory=dict)
    trace_id: str = ""


@dataclass(frozen=True)
class HermesResponse:
    text: str
    provider: str = "fake"
    model: str = "fake-model"
    latency_ms: int = 0
    fallback_count: int = 0
    fallback_reason: str | None = None
    diagnostics: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AssistantReply:
    text: str
    intent: Intent
    provider: str | None = None
    model: str | None = None
    fallback_count: int = 0
    blocked_reason: str | None = None
    perf_ms: dict[str, int] = field(default_factory=dict)
    trace_id: str = ""
    buttons: list[list[ReplyButton]] = field(default_factory=list)
    suppress_delivery: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
