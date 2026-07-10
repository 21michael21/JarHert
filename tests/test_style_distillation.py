from __future__ import annotations

from assistant.style_distillation import distill_style_profile, extract_assistant_messages, message_weight


def test_long_source_posts_have_capped_lower_weight_than_short_posts() -> None:
    assert message_weight("короткое сообщение") == 1.0
    assert message_weight("длинный " * 1_000) < 0.1


def test_distilled_profile_keeps_runtime_answers_short_without_copying_source_text() -> None:
    source = [
        "Сначала проверь ограничение. Потом выбирай решение.",
        "Секретная авторская формулировка, которую нельзя переносить в профиль.",
        "длинный текст " * 800,
    ]

    profile = distill_style_profile(source, max_response_chars=500)

    assert profile.source_messages == 3
    assert profile.long_message_count == 1
    assert profile.effective_weight < 3
    assert "Обычно отвечай 1–4 короткими предложениями" in profile.prompt
    assert "500 символов" in profile.prompt
    assert "Не называй возможную причину фактом без данных" in profile.prompt
    assert "Для просьбы написать сообщение дай готовый текст" in profile.prompt
    assert "Секретная авторская формулировка" not in profile.prompt


def test_distillation_uses_only_assistant_messages_from_chatml_rows() -> None:
    rows = [
        {
            "messages": [
                {"role": "system", "content": "Правила"},
                {"role": "user", "content": "Личный запрос"},
                {"role": "assistant", "content": "Ответ автора"},
            ]
        }
    ]

    assert extract_assistant_messages(rows) == ["Ответ автора"]
