from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TrainingFeedbackKind(StrEnum):
    NORMAL = "normal"
    EDIT = "edit"


class TrainingFeedbackStatus(StrEnum):
    PENDING_EDIT = "pending_edit"
    APPROVED = "approved"


@dataclass(frozen=True)
class TrainingExample:
    id: int
    user_id: int
    conversation_turn_id: int
    user_text: str
    assistant_text: str | None
    feedback_kind: TrainingFeedbackKind
    status: TrainingFeedbackStatus
    created_at: datetime
    approved_at: datetime | None
