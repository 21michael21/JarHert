from __future__ import annotations

import re

from assistant.types import AssistantReply, Intent


BLOCKED_TEXTS = {
    "empty_input": "Напиши текст после команды.",
    "input_too_long": "Сообщение слишком длинное. Сократи его и отправь ещё раз.",
    "dangerous_action_requested": "Я не выполняю действия с сервером, файлами, ключами и shell-командами. Могу безопасно помочь с планом или чек-листом.",
}


class ResponseComposer:
    def success_summary(self, *, done: list[str], intent: Intent) -> AssistantReply:
        lines = ["Сделал:"]
        lines.extend(f"{index}. {_clean_line(item)}" for index, item in enumerate(done, start=1))
        return AssistantReply(text="\n".join(lines), intent=intent)

    def partial_failure(self, *, done: list[str], failed: list[str], intent: Intent) -> AssistantReply:
        parts: list[str] = []
        if done:
            lines = ["Сделал:"]
            lines.extend(f"{index}. {_clean_line(item)}" for index, item in enumerate(done, start=1))
            parts.append("\n".join(lines))
        if failed:
            lines = ["Не получилось:"]
            lines.extend(_clean_failure(item) for item in failed)
            parts.append("\n".join(lines))
        return AssistantReply(text="\n\n".join(parts), intent=intent)

    def clarification_question(
        self,
        reason: str = "natural_action_needs_clarification",
        *,
        intent: Intent = Intent.AGENT_DO,
    ) -> AssistantReply:
        return AssistantReply(
            text="Уточни действие, объект и время.",
            intent=intent,
            blocked_reason=reason,
        )

    def blocked_action(self, reason: str, *, intent: Intent) -> AssistantReply:
        return AssistantReply(
            text=BLOCKED_TEXTS.get(reason, "Не могу обработать этот запрос."),
            intent=intent,
            blocked_reason=reason,
        )

    def provider_fallback(
        self,
        *,
        reason: str,
        intent: Intent,
        provider: str | None = None,
        model: str | None = None,
        fallback_count: int = 0,
    ) -> AssistantReply:
        return AssistantReply(
            text="AI-ответ был плохого качества. Попробуй переформулировать запрос.",
            intent=intent,
            provider=provider,
            model=model,
            fallback_count=fallback_count,
            blocked_reason=reason,
        )

    def provider_unavailable(self, *, intent: Intent) -> AssistantReply:
        return AssistantReply(
            text="AI сейчас не ответил. Попробуй ещё раз позже.",
            intent=intent,
            blocked_reason="hermes_unavailable",
        )

    def daily_limit(self, *, intent: Intent) -> AssistantReply:
        return AssistantReply(
            text="Лимит бесплатных AI-запросов на сегодня закончился. Попробуй завтра.",
            intent=intent,
            blocked_reason="daily_limit_exceeded",
        )


def _clean_line(value: str, *, limit: int = 220) -> str:
    text = " ".join((value or "").strip().split())
    text = _strip_technical_dump(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clean_failure(value: str) -> str:
    text = _clean_line(value, limit=180)
    if ":" not in text:
        return text
    prefix, _, message = text.partition(":")
    if _looks_technical(message):
        return f"{prefix}: ошибка внешнего сервиса"
    return text


def _strip_technical_dump(value: str) -> str:
    text = re.sub(r"traceback \(most recent call last\).*", "ошибка внешнего сервиса", value, flags=re.IGNORECASE)
    text = re.sub(r"\b[A-Za-z_]+Error\([^)]*\)", "ошибка внешнего сервиса", text)
    text = re.sub(r"\b[A-Za-z_]+Error:.*", "ошибка внешнего сервиса", text)
    return text


def _looks_technical(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("traceback", "runtimeerror", "valueerror", "stack", "{", "}"))
