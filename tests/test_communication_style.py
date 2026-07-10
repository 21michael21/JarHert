from __future__ import annotations

from assistant.communication_style import (
    CommunicationStyleGuide,
    constrain_response_length,
    load_communication_style,
)
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.provider_clients import FakeHermesClient
from assistant.types import UserContext


def user() -> UserContext:
    return UserContext(user_id=1, tg_user_id=1001)


def test_ask_sends_style_as_system_prompt_without_rewriting_user_text() -> None:
    hermes = FakeHermesClient()
    guide = CommunicationStyleGuide("Начинай с конкретного вывода.", version="test-v1")
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        communication_style=guide,
    )

    pipeline.handle_text(user(), "/ask объясни MVP")

    request = hermes.requests[-1]
    assert request.prompt == "объясни MVP"
    assert "Начинай с конкретного вывода." in request.system_prompt
    assert "явный формат и длина из запроса важнее" in request.system_prompt
    assert request.context["style_profile"] == "test-v1"


def test_detailed_preference_changes_only_style_overlay() -> None:
    guide = CommunicationStyleGuide("Базовый стиль.", version="test-v1")

    rendered = guide.render("detailed")

    assert rendered.startswith("Базовый стиль.")
    assert "Детальный режим" in rendered


def test_concise_overlay_prioritizes_explicit_user_length() -> None:
    guide = CommunicationStyleGuide("Базовый стиль.", version="test-v1")

    rendered = guide.render("concise")

    assert "явный формат и длина из запроса важнее" in rendered
    assert "не добавляй служебные заголовки" in rendered.lower()
    assert "240 символов" in rendered
    assert "500 символов" in rendered


def test_expressive_preference_allows_lively_language_with_boundaries() -> None:
    guide = CommunicationStyleGuide("Базовый стиль.", version="test-v1")

    rendered = guide.render("expressive")

    assert "живой разговорный тон" in rendered
    assert "русский мат как обычную часть голоса" in rendered
    assert "не стерилизуй ответ" in rendered.lower()
    assert "не уходи в оскорбления" in rendered.lower()
    assert "без корпоративной ваты" in rendered.lower()


def test_disabled_style_guide_returns_empty_system_prompt() -> None:
    guide = load_communication_style(enabled=False)

    assert guide.enabled is False
    assert guide.render("short") == ""
    assert guide.budget("ответь коротко", "short", max_chars=2500).max_chars == 2500


def test_custom_style_path_is_loaded_and_versioned(tmp_path) -> None:
    style_path = tmp_path / "style.md"
    style_path.write_text("Говори прямо и спокойно.", encoding="utf-8")

    first = load_communication_style(enabled=True, path=str(style_path))
    second = load_communication_style(enabled=True, path=str(style_path))

    assert first.prompt == "Говори прямо и спокойно."
    assert first.version == second.version
    assert first.version.startswith("style-")


def test_profile_declared_response_limit_is_enforced_for_ordinary_answers(tmp_path) -> None:
    style_path = tmp_path / "style.md"
    style_path.write_text(
        "<!-- jarhert-style max_response_chars=420 -->\nГовори прямо и спокойно.",
        encoding="utf-8",
    )

    guide = load_communication_style(enabled=True, path=str(style_path))
    budget = guide.budget("Объясни, что делать", "concise", max_chars=2500)

    assert guide.max_response_chars == 420
    assert "jarhert-style" not in guide.prompt
    assert budget.max_chars == 420


def test_profile_short_request_uses_a_stricter_short_limit(tmp_path) -> None:
    style_path = tmp_path / "style.md"
    style_path.write_text(
        "<!-- jarhert-style max_response_chars=320 -->\nГовори прямо и спокойно.",
        encoding="utf-8",
    )

    guide = load_communication_style(enabled=True, path=str(style_path))

    assert guide.budget("Ответь коротко: что делать?", "concise", max_chars=2500).max_chars == 240


def test_profile_version_changes_when_declared_response_limit_changes(tmp_path) -> None:
    first_path = tmp_path / "first.md"
    second_path = tmp_path / "second.md"
    first_path.write_text("<!-- jarhert-style max_response_chars=320 -->\nГовори прямо.", encoding="utf-8")
    second_path.write_text("<!-- jarhert-style max_response_chars=420 -->\nГовори прямо.", encoding="utf-8")

    first = load_communication_style(enabled=True, path=str(first_path))
    second = load_communication_style(enabled=True, path=str(second_path))

    assert first.version != second.version


def test_explicit_short_request_gets_transport_level_budget() -> None:
    guide = CommunicationStyleGuide("Базовый стиль.", version="test-v1")

    budget = guide.budget("Ответь коротко: зачем нужен retry?", "concise", max_chars=2500)

    assert budget.max_chars == 360
    assert budget.max_output_tokens == 100


def test_response_constraint_keeps_complete_sentence() -> None:
    text = "Сначала обнови OAuth. Потом повтори health-check. Если ошибка осталась, проверь логи."

    constrained = constrain_response_length(text, max_chars=52)

    assert constrained == "Сначала обнови OAuth. Потом повтори health-check."
    assert len(constrained) <= 52


def test_response_constraint_never_exceeds_hard_limit_at_sentence_boundary() -> None:
    text = "а" * 260 + ". хвост"

    constrained = constrain_response_length(text, max_chars=260)

    assert len(constrained) <= 260


def test_pipeline_applies_response_budget_after_provider_reply() -> None:
    from assistant.types import HermesResponse

    hermes = FakeHermesClient(
        [
            HermesResponse(
                text="Первый полезный ответ. "
                + " ".join(f"Подробность номер {index}." for index in range(1, 100))
            )
        ]
    )
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        communication_style=CommunicationStyleGuide("Отвечай прямо.", version="test-v1"),
    )

    reply = pipeline.handle_text(user(), "/ask ответь коротко: что делать?")

    assert reply.text.startswith("Первый полезный ответ.")
    assert len(reply.text) <= 360
    assert hermes.requests[-1].max_output_tokens == 100


def test_pipeline_applies_profile_limit_without_explicit_short_request() -> None:
    from assistant.types import HermesResponse

    hermes = FakeHermesClient([HermesResponse(text="Полезный ответ. " + "Деталь. " * 200)])
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        communication_style=CommunicationStyleGuide(
            "Отвечай прямо.",
            version="test-v1",
            max_response_chars=420,
        ),
    )

    reply = pipeline.handle_text(user(), "/ask объясни, что делать")

    assert len(reply.text) <= 420


def test_response_budget_does_not_hide_unsafe_tail() -> None:
    from assistant.types import HermesResponse

    hermes = FakeHermesClient(
        [HermesResponse(text="Безопасное начало. " + "пояснение " * 80 + "Выполни rm -rf / на сервере.")]
    )
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        communication_style=CommunicationStyleGuide("Отвечай прямо.", version="test-v1"),
    )

    reply = pipeline.handle_text(user(), "/ask ответь коротко")

    assert "плохого качества" in reply.text
