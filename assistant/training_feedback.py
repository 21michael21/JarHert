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
    preference_reason: str | None
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


def explain_preference(user_text: str, rejected: str, chosen: str) -> str:
    rejected_clean = _normalized_text(rejected)
    chosen_clean = _normalized_text(chosen)
    reasons: list[str] = []
    if len(chosen_clean) < len(rejected_clean) * 0.85:
        reasons.append("Ответ короче и без повторов.")
    if _is_more_concrete(rejected_clean, chosen_clean):
        reasons.append("Есть конкретный следующий шаг.")
    if _has_ai_filler(rejected_clean) and not _has_ai_filler(chosen_clean):
        reasons.append("Убрано гладкое вводное без пользы.")
    if rejected_clean.endswith("?") and not chosen_clean.endswith("?"):
        reasons.append("Не добавлен лишний вопрос.")
    response_type = classify_training_example_type(user_text, chosen)
    if response_type is TrainingExampleType.MESSAGE_DRAFT:
        reasons.append("Оставлен только готовый текст сообщения.")
    elif response_type is TrainingExampleType.INSUFFICIENT_DATA:
        reasons.append("Не выдумывает причину без данных.")
    elif response_type is TrainingExampleType.SAFE_REFUSAL:
        reasons.append("Сохраняет безопасную границу.")
    return " ".join(reasons[:2]) or "Ответ прямее и полезнее для запроса."


def is_distinct_preference_pair(rejected: str, chosen: str) -> bool:
    return bool(_normalized_text(rejected) and _normalized_text(chosen) and _normalized_text(rejected) != _normalized_text(chosen))


def _normalized_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _has_ai_filler(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "конечно",
            "с радостью",
            "в целом",
            "комплексно",
            "важно отметить",
            "давайте разбер",
        )
    )


def _is_more_concrete(rejected: str, chosen: str) -> bool:
    action_markers = ("проверь", "сделай", "напиши", "создай", "выбери", "запусти", "сначала")
    return any(marker in chosen for marker in action_markers) and not any(marker in rejected for marker in action_markers)
