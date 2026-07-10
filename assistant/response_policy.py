from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ResponseMode(StrEnum):
    DEFAULT = "default"
    MESSAGE_DRAFT = "message_draft"
    UNKNOWN_CAUSE = "unknown_cause"


@dataclass(frozen=True)
class ResponsePolicy:
    mode: ResponseMode
    instructions: str = ""

    def normalize(self, text: str) -> str:
        clean = (text or "").strip()
        if self.mode == ResponseMode.MESSAGE_DRAFT:
            quoted = re.search(r"«([^»]+)»", clean)
            if quoted:
                suffix = clean[quoted.end() :].lstrip()
                punctuation = suffix[0] if suffix[:1] in {".", "!", "?"} else ""
                return (quoted.group(1).strip() + punctuation).strip()
            clean = re.sub(
                r"^(?:вот\s+вариант|сообщение|можно\s+так)\s*[:—-]\s*",
                "",
                clean,
                flags=re.IGNORECASE,
            )
            return _remove_trailing_follow_up(clean)
        if self.mode == ResponseMode.UNKNOWN_CAUSE:
            sentences = re.split(r"(?<=[.!?])\s+", clean)
            clean = " ".join(
                sentence
                for sentence in sentences
                if not any(
                    marker in sentence.lower()
                    for marker in ("вероятно", "возможно", "может быть", "скорее всего", "похоже", "предполаг")
                )
            )
            return _remove_trailing_follow_up(clean)
        return _remove_trailing_follow_up(clean)


def classify_response_policy(prompt: str) -> ResponsePolicy:
    normalized = (prompt or "").strip().lower()
    if _is_message_draft(normalized):
        return ResponsePolicy(
            ResponseMode.MESSAGE_DRAFT,
            "Это просьба сформулировать сообщение. Верни только готовый текст сообщения: без «вот вариант», "
            "без списка альтернатив и без вопроса в конце.",
        )
    if _is_unknown_cause(normalized):
        return ResponsePolicy(
            ResponseMode.UNKNOWN_CAUSE,
            "Причина неизвестна из-за отсутствия логов или данных. Не называй возможные причины фактом и не "
            "перечисляй догадки. Честно скажи, что причину нельзя установить, затем дай 1–3 проверяемых шага. "
            "Не заканчивай вопросом.",
        )
    return ResponsePolicy(
        ResponseMode.DEFAULT,
        "Не заканчивай ответ вопросом, если без уточнения уже можно дать корректный полезный ответ.",
    )


def _is_message_draft(prompt: str) -> bool:
    has_writing_request = any(
        marker in prompt
        for marker in ("напиши", "написать", "сформулируй", "составь сообщение")
    )
    has_recipient_or_message = any(
        marker in prompt
        for marker in ("сообщени", "написать ", "письмо", "илье", "маме", "ему", "ей", "коллеге")
    )
    return has_writing_request and has_recipient_or_message


def _is_unknown_cause(prompt: str) -> bool:
    return any(
        marker in prompt
        for marker in ("без лог", "нет лог", "логов нет", "логов у тебя нет", "без данных", "данных у тебя нет")
    )


def _remove_trailing_follow_up(text: str) -> str:
    return re.sub(
        r"(?:\s+|\n+)(?:хочешь|если\s+хочешь|могу|нужен\s+ли)\b[^?]{0,180}\?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
