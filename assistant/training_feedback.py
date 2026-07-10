from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from assistant.response_policy import ResponseMode, classify_response_policy


class TrainingFeedbackKind(StrEnum):
    NORMAL = "normal"
    EDIT = "edit"


class TrainingFeedbackStatus(StrEnum):
    PENDING_EDIT = "pending_edit"
    APPROVED = "approved"


class TrainingExampleType(StrEnum):
    SHORT_ANSWER = "short_answer"
    PLAN_DECISION = "plan_decision"
    MESSAGE_DRAFT = "message_draft"
    INSUFFICIENT_DATA = "insufficient_data"
    CLARIFICATION = "clarification"
    SAFE_REFUSAL = "safe_refusal"


@dataclass(frozen=True)
class TrainingExample:
    id: int
    user_id: int
    conversation_turn_id: int
    user_text: str
    assistant_text: str | None
    rejected_assistant_text: str | None
    feedback_kind: TrainingFeedbackKind
    example_type: TrainingExampleType
    status: TrainingFeedbackStatus
    created_at: datetime
    approved_at: datetime | None


def classify_training_example_type(user_text: str, assistant_text: str) -> TrainingExampleType:
    prompt = (user_text or "").strip().lower()
    response = (assistant_text or "").strip().lower()
    if _is_safe_refusal(response):
        return TrainingExampleType.SAFE_REFUSAL
    policy = classify_response_policy(prompt)
    if policy.mode is ResponseMode.MESSAGE_DRAFT:
        return TrainingExampleType.MESSAGE_DRAFT
    if policy.mode is ResponseMode.UNKNOWN_CAUSE:
        return TrainingExampleType.INSUFFICIENT_DATA
    if response.endswith("?") or response.startswith("уточни"):
        return TrainingExampleType.CLARIFICATION
    if any(marker in prompt for marker in ("план", "стратег", "решени", "выбрать", "сравни", "приоритет", "что делать")):
        return TrainingExampleType.PLAN_DECISION
    return TrainingExampleType.SHORT_ANSWER


def _is_safe_refusal(response: str) -> bool:
    return any(
        marker in response
        for marker in (
            "я не выполняю действия с сервером",
            "не могу помочь с опасным",
            "не могу обработать этот запрос",
        )
    )
