from __future__ import annotations

from assistant.response_composer import ResponseComposer
from assistant.types import Intent


def test_composer_formats_success_summary() -> None:
    reply = ResponseComposer().success_summary(
        done=["Сохранил идею #1.", "Поставил напоминание #1."],
        intent=Intent.AGENT_DO,
    )

    assert reply.text == "Сделал:\n1. Сохранил идею #1.\n2. Поставил напоминание #1."
    assert reply.intent == Intent.AGENT_DO
    assert reply.blocked_reason is None


def test_composer_formats_partial_failure_without_trace_dump() -> None:
    reply = ResponseComposer().partial_failure(
        done=["Сохранил идею #1."],
        failed=["2. deploy: Traceback (most recent call last): RuntimeError('secret stack')"],
        intent=Intent.AGENT_DO,
    )

    assert "Сделал:" in reply.text
    assert "Не получилось:" in reply.text
    assert "Traceback" not in reply.text
    assert "RuntimeError" not in reply.text
    assert reply.blocked_reason is None


def test_composer_formats_clarification_and_blocked_action() -> None:
    composer = ResponseComposer()

    clarification = composer.clarification_question("natural_action_needs_clarification")
    blocked = composer.blocked_action("dangerous_action_requested", intent=Intent.ASK)

    assert clarification.text == "Уточни действие, объект и время."
    assert clarification.blocked_reason == "natural_action_needs_clarification"
    assert "сервером" in blocked.text
    assert blocked.blocked_reason == "dangerous_action_requested"


def test_composer_formats_provider_fallback_without_provider_dump() -> None:
    reply = ResponseComposer().provider_fallback(
        reason="raw_provider_error",
        intent=Intent.ASK,
        provider="openrouter",
        model="free-model",
        fallback_count=2,
    )

    assert reply.text == "AI-ответ был плохого качества. Попробуй переформулировать запрос."
    assert "openrouter" not in reply.text
    assert reply.provider == "openrouter"
    assert reply.model == "free-model"
    assert reply.fallback_count == 2
    assert reply.blocked_reason == "raw_provider_error"


def test_composer_daily_limit_text_is_not_free_provider_specific() -> None:
    reply = ResponseComposer().daily_limit(intent=Intent.ASK)

    assert reply.text == "AI-лимит на сегодня закончился. Если это твой бот, увеличь лимит в env или отключи его."
    assert "бесплат" not in reply.text.lower()
    assert reply.blocked_reason == "daily_limit_exceeded"
