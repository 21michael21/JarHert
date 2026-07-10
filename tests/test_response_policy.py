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


def test_default_policy_removes_obvious_extra_follow_up_question() -> None:
    policy = classify_response_policy("А как ты умеешь материться и писать?")

    response = policy.normalize(
        "Могу писать живо, коротко и с лёгким матом, если это уместно. Что тебе нужно: жесткий стиль или нейтральный формат?"
    )

    assert response == "Могу писать живо, коротко и с лёгким матом, если это уместно."


def test_default_policy_removes_trailing_invitation_sentence() -> None:
    policy = classify_response_policy("А как ты умеешь материться и писать?")

    response = policy.normalize(
        "Да, могу без ваты и с нужным колоритом. Скажешь стиль и тему — подстрою."
    )

    assert response == "Да, могу без ваты и с нужным колоритом."


def test_default_policy_removes_trailing_invitation_even_without_question_mark() -> None:
    policy = classify_response_policy("А как ты умеешь материться и писать?")

    response = policy.normalize(
        "Так и есть — могу писать резко и по делу, без лишних слов. Хочешь, дам пример живого варианта или расскажи задачу — подскажу стиль."
    )

    assert response == "Так и есть — могу писать резко и по делу, без лишних слов."
