from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable


LONG_MESSAGE_CHARS = 1_200
WEIGHT_CAP_CHARS = 500


@dataclass(frozen=True)
class DistilledStyleProfile:
    prompt: str
    source_messages: int
    long_message_count: int
    effective_weight: float
    version: str


def message_weight(text: str, *, cap_chars: int = WEIGHT_CAP_CHARS) -> float:
    """Cap the influence of a source post so long publications do not dominate."""
    length = len((text or "").strip())
    if length <= cap_chars:
        return 1.0
    return round(cap_chars / length, 4)


def extract_assistant_messages(rows: Iterable[dict[str, Any]]) -> list[str]:
    messages: list[str] = []
    for row in rows:
        for message in row.get("messages", []):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                messages.append(content)
    return messages


def distill_style_profile(
    messages: Iterable[str],
    *,
    max_response_chars: int = 500,
) -> DistilledStyleProfile:
    clean_messages = [str(message).strip() for message in messages if str(message).strip()]
    if not clean_messages:
        raise ValueError("Style distillation needs at least one non-empty assistant message")
    if max_response_chars < 160:
        raise ValueError("max_response_chars must be at least 160")

    long_message_count = sum(len(message) >= LONG_MESSAGE_CHARS for message in clean_messages)
    effective_weight = round(sum(message_weight(message) for message in clean_messages), 4)
    question_share = _weighted_share(clean_messages, lambda message: "?" in message)
    condition_share = _weighted_share(
        clean_messages,
        lambda message: any(token in message.lower() for token in ("если ", "когда ", "иначе", "но ")),
    )

    prompt = _render_prompt(
        max_response_chars=max_response_chars,
        question_share=question_share,
        condition_share=condition_share,
    )
    version_source = f"{prompt}|{len(clean_messages)}|{long_message_count}|{effective_weight}"
    return DistilledStyleProfile(
        prompt=prompt,
        source_messages=len(clean_messages),
        long_message_count=long_message_count,
        effective_weight=effective_weight,
        version=f"distilled-{hashlib.sha256(version_source.encode('utf-8')).hexdigest()[:12]}",
    )


def _weighted_share(messages: list[str], predicate) -> int:
    total_weight = sum(message_weight(message) for message in messages)
    if not total_weight:
        return 0
    matched_weight = sum(message_weight(message) for message in messages if predicate(message))
    return round(matched_weight / total_weight * 100)


def _render_prompt(*, max_response_chars: int, question_share: int, condition_share: int) -> str:
    question_rule = (
        "Задавай один короткий вопрос только когда без него изменится решение."
        if question_share >= 10
        else "Не задавай вопрос по привычке; уточняй только то, что меняет решение."
    )
    condition_rule = (
        "Если есть риск или развилка, называй условие простыми словами."
        if condition_share >= 15
        else "Добавляй условие или риск только когда без него ответ может навредить."
    )
    return (
        "Ты русскоязычный личный помощник. Пиши как живой умный собеседник, а не как канал, "
        "справочник или корпоративный бот. Не выдавай себя за автора источника.\n\n"
        "Сначала ответь на конкретную просьбу человека: дай решение, факт или ближайшее действие. "
        f"Обычно отвечай 1–4 короткими предложениями, не длиннее {max_response_chars} символов. "
        "Если пользователь просит коротко, уложись в 1–2 предложения. Для сложной задачи: сначала "
        "что делать сейчас, затем одна причина или риск, затем следующий шаг.\n\n"
        f"{question_rule} {condition_rule} "
        "Не называй возможную причину фактом без данных: честно скажи, чего не хватает, и дай проверяемый шаг. "
        "Для просьбы написать сообщение дай готовый текст без вариантов и вопроса в конце, если данных достаточно. "
        "Говори прямо и спокойно. Не начинай с «Конечно», «С удовольствием» или «Давай разберёмся». "
        "Не добавляй пустые заголовки, рекламный тон, повтор вывода, мета-комментарии о себе или "
        "искусственные длинные вступления. Стиль не отменяет безопасность, подтверждения действий "
        "и честное признание неопределённости."
    )
