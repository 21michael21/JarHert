from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActionType(str, Enum):
    ASK_ANSWER = "ask.answer"
    IDEA_SAVE = "idea.save"
    MEMORY_SAVE = "memory.save"
    REMINDER_CREATE = "reminder.create"
    TASK_CREATE = "task.create"
    TASK_LIST = "task.list"
    TASK_MOVE = "task.move"
    TASK_DONE = "task.done"
    CALENDAR_CREATE = "calendar.create"
    CALENDAR_MOVE = "calendar.move"
    TELEGRAM_REPLY = "telegram.reply"
    TELEGRAM_SEND_MESSAGE = "telegram.send_message"
    AGENT_JOB_CREATE = "agent.job.create"


@dataclass(frozen=True)
class PlannedAction:
    type: ActionType
    payload: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    needs_confirmation: bool = False
    reason: str = "deterministic_rule"


@dataclass(frozen=True)
class NaturalRoute:
    actions: list[PlannedAction] = field(default_factory=list)
    fallback_to_ai: bool = True
    reason: str = "no_action"
