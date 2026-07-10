from __future__ import annotations

from collections import Counter
from typing import Iterable

from assistant.training_data import build_consented_record
from assistant.training_feedback import TrainingExample, TrainingExampleType, TrainingFeedbackStatus


TARGET_COUNTS = {
    TrainingExampleType.SHORT_ANSWER: 120,
    TrainingExampleType.PLAN_DECISION: 50,
    TrainingExampleType.MESSAGE_DRAFT: 40,
    TrainingExampleType.INSUFFICIENT_DATA: 30,
    TrainingExampleType.CLARIFICATION: 30,
    TrainingExampleType.SAFE_REFUSAL: 30,
    "preference_pairs": 40,
}


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
            "example_type": example.example_type.value,
        }
        records.append(record)
    return records


def split_approved_feedback_records(
    *,
    system_prompt: str,
    examples: Iterable[TrainingExample],
) -> dict[TrainingExampleType, list[dict]]:
    groups = {example_type: [] for example_type in TrainingExampleType}
    sft_examples = [
        example
        for example in examples
        if example.status is TrainingFeedbackStatus.APPROVED and not example.rejected_assistant_text
    ]
    for record in build_approved_feedback_records(system_prompt=system_prompt, examples=sft_examples):
        groups[TrainingExampleType(record["metadata"]["example_type"])].append(record)
    return groups


def build_preference_records(examples: Iterable[TrainingExample]) -> list[dict]:
    records: list[dict] = []
    for example in examples:
        if (
            example.status is not TrainingFeedbackStatus.APPROVED
            or not example.assistant_text
            or not example.rejected_assistant_text
        ):
            continue
        records.append(
            {
                "prompt": example.user_text,
                "chosen": example.assistant_text,
                "rejected": example.rejected_assistant_text,
                "metadata": {
                    "source": "explicit_telegram_feedback",
                    "example_id": example.id,
                    "example_type": example.example_type.value,
                },
            }
        )
    return records


def training_feedback_progress(examples: Iterable[TrainingExample]) -> dict:
    approved = [example for example in examples if example.status is TrainingFeedbackStatus.APPROVED]
    sft_examples = [example for example in approved if not example.rejected_assistant_text]
    counts = Counter(example.example_type.value for example in sft_examples)
    counts["preference_pairs"] = len(build_preference_records(approved))
    normalized_counts = {key.value if isinstance(key, TrainingExampleType) else key: counts[key.value if isinstance(key, TrainingExampleType) else key] for key in TARGET_COUNTS}
    gaps = {
        key.value if isinstance(key, TrainingExampleType) else key: max(
            0,
            target - normalized_counts[key.value if isinstance(key, TrainingExampleType) else key],
        )
        for key, target in TARGET_COUNTS.items()
    }
    return {
        "counts": normalized_counts,
        "targets": {key.value if isinstance(key, TrainingExampleType) else key: value for key, value in TARGET_COUNTS.items()},
        "gaps": gaps,
        "target_ready": not any(gaps.values()),
    }
