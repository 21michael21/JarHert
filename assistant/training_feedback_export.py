from __future__ import annotations

from typing import Iterable

from assistant.training_data import build_consented_record
from assistant.training_feedback import TrainingExample, TrainingFeedbackStatus


def build_approved_feedback_records(
    *,
    system_prompt: str,
    examples: Iterable[TrainingExample],
) -> list[dict]:
    records: list[dict] = []
    seen_ids: set[int] = set()
    for example in examples:
        if (
            example.id in seen_ids
            or example.status is not TrainingFeedbackStatus.APPROVED
            or not example.assistant_text
        ):
            continue
        seen_ids.add(example.id)
        record = build_consented_record(
            system_prompt=system_prompt,
            user_text=example.user_text,
            assistant_text=example.assistant_text,
            source_turn_id=example.conversation_turn_id,
        )
        record["metadata"] = {
            "source": "explicit_telegram_feedback",
            "example_id": example.id,
            "feedback_kind": example.feedback_kind.value,
        }
        records.append(record)
    return records
