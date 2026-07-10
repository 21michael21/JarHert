from __future__ import annotations

from assistant.response_policy import ResponseMode, classify_response_policy


def test_message_request_returns_only_ready_message_without_follow_up_question() -> None:
    policy = classify_response_policy("Нужно написать Илье, что я задержусь. Сформулируй нормально.")

    response = policy.normalize("Вот вариант: «Илья, задержусь на 20 минут. Извини». Хочешь сделать формальнее?")

    assert policy.mode == ResponseMode.MESSAGE_DRAFT
    assert response == "Илья, задержусь на 20 минут. Извини."


def test_unknown_cause_policy_forbids_speculation_and_trailing_question() -> None:
    policy = classify_response_policy("Почему вчера упал сервис? Логов нет.")

    response = policy.normalize(
        "Без логов причину не установить. Можно только предполагать, что упала очередь. Проверь ошибки и метрики. Хочешь чек-лист?"
    )

    assert policy.mode == ResponseMode.UNKNOWN_CAUSE
    assert "Не называй возможные причины" in policy.instructions
    assert "Не заканчивай вопросом" in policy.instructions
    assert response == "Без логов причину не установить. Проверь ошибки и метрики."
