from __future__ import annotations

from dataclasses import dataclass

from assistant.action_schema import ActionType, PlannedAction
from assistant.quality_gates import check_input


REQUIRED_FIELDS = {
    ActionType.IDEA_SAVE: ("text",),
    ActionType.MEMORY_SAVE: ("text",),
    ActionType.REMINDER_CREATE: ("text",),
    ActionType.TASK_CREATE: ("title",),
    ActionType.TASK_LIST: (),
    ActionType.TASK_MOVE: ("title", "to"),
    ActionType.TASK_DONE: ("title",),
    ActionType.CALENDAR_CREATE: ("title", "start", "end"),
    ActionType.CALENDAR_MOVE: ("title", "start"),
    ActionType.TELEGRAM_REPLY: ("text",),
    ActionType.AGENT_JOB_CREATE: ("goal",),
}


@dataclass(frozen=True)
class ActionValidationResult:
    actions: list[PlannedAction]
    ok: bool
    reason: str = ""


def validate_actions(
    actions: list[PlannedAction],
    *,
    min_confidence: float = 0.75,
) -> ActionValidationResult:
    if not actions:
        return ActionValidationResult([], False, "empty_actions")

    valid: list[PlannedAction] = []
    for action in actions:
        if action.confidence < min_confidence:
            return ActionValidationResult([], False, "llm_low_confidence")
        required = REQUIRED_FIELDS.get(action.type)
        if required is None:
            return ActionValidationResult([], False, "unsupported_action")
        for field in required:
            if not str(action.payload.get(field) or "").strip():
                return ActionValidationResult([], False, "missing_required_field")
        if not _payload_is_safe(action):
            return ActionValidationResult([], False, "action_validation_failed")
        valid.append(action)
    return ActionValidationResult(valid, True)


def _payload_is_safe(action: PlannedAction) -> bool:
    for value in action.payload.values():
        if not isinstance(value, str):
            return False
        if not check_input(value, max_chars=2000).ok:
            return False
    return True
