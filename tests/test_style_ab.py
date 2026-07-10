from __future__ import annotations

from pathlib import Path

from assistant.response_policy import classify_response_policy
from assistant.style_ab import StyleCase, choose_winner, is_promotion_eligible, load_style_cases, score_style_response
from assistant.types import HermesResponse
from scripts.eval_style_ab import run_variant


class _FakeClient:
    def ask(self, request):
        return HermesResponse(text="Сообщение: Привет, задержусь на 15 минут. Хочешь сделать формальнее?", latency_ms=1)


def test_style_ab_scores_short_direct_answer_against_case_criteria() -> None:
    case = StyleCase(
        id="oauth",
        prompt="OAuth протух, что делать?",
        max_chars=260,
        required_any=("обнов", "токен"),
    )

    score = score_style_response(case, "Сначала обнови токен OAuth. Потом повтори health-check.")

    assert score.ok is True
    assert score.score >= 90
    assert score.issues == ()


def test_style_ab_rejects_generic_long_reply() -> None:
    case = StyleCase(id="short", prompt="Что делать?", max_chars=80)

    score = score_style_response(
        case,
        "Конечно! С удовольствием помогу. Давайте разберёмся в этом важном и многогранном вопросе.",
    )

    assert score.ok is False
    assert "too_long" in score.issues
    assert "style_slop" in score.issues


def test_style_ab_winner_requires_a_real_quality_advantage() -> None:
    assert choose_winner(base_score=90, candidate_score=90) == "no_change"
    assert choose_winner(base_score=90, candidate_score=94) == "candidate"
    assert choose_winner(base_score=94, candidate_score=90) == "base"


def test_style_profile_is_not_promoted_on_relative_win_without_absolute_quality() -> None:
    assert is_promotion_eligible(average_score=89, passed=58, total=100) is False
    assert is_promotion_eligible(average_score=88, passed=85, total=100) is True


def test_style_ab_suite_has_one_hundred_realistic_russian_cases() -> None:
    fixture = Path(__file__).with_name("style_ab_cases.json")

    cases = load_style_cases(fixture)

    assert len(cases) == 100
    assert all(case.prompt and case.max_chars <= 500 for case in cases)


def test_style_ab_scores_the_normalized_runtime_message() -> None:
    case = StyleCase(id="message", prompt="Нужно написать Илье, что я задержусь.", max_chars=220)

    result = run_variant(
        _FakeClient(),
        case,
        "Отвечай по делу.",
        response_policy=classify_response_policy(case.prompt),
        max_output_chars=420,
    )

    assert result.answer == "Привет, задержусь на 15 минут."
    assert result.ok is True
