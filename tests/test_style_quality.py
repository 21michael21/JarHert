from __future__ import annotations

import json

from assistant.quality_gates import check_output
from assistant.style_quality import assess_communication_style
from assistant.types import GateStatus
from scripts.eval_style import evaluate_cases


def test_direct_specific_reply_passes_style_assessment() -> None:
    assessment = assess_communication_style(
        "Сначала проверь срок токена. Если он истёк, обнови OAuth и повтори health-check."
    )

    assert assessment.ok is True
    assert assessment.score >= 85
    assert assessment.issues == ()


def test_generic_ai_preamble_fails_style_assessment() -> None:
    assessment = assess_communication_style(
        "Конечно! С удовольствием помогу. Давайте разберёмся и погрузимся в этот важный вопрос."
    )

    assert assessment.ok is False
    assert assessment.score < 60
    assert "generic_preamble" in assessment.issues


def test_runtime_output_gate_rejects_severe_style_slop() -> None:
    result = check_output(
        "Конечно! С удовольствием помогу. Давайте разберёмся и погрузимся в этот важный вопрос."
    )

    assert result.status == GateStatus.NEEDS_FALLBACK
    assert result.reason == "style_slop"


def test_style_eval_fixture_has_no_misclassified_cases(tmp_path) -> None:
    cases = [
        {"id": "direct", "text": "Сначала проверь логи. Потом повтори запрос.", "expected_ok": True},
        {
            "id": "slop",
            "text": "Конечно! С удовольствием помогу. Давайте разберёмся и погрузимся в тему.",
            "expected_ok": False,
        },
    ]
    fixture = tmp_path / "style.json"
    fixture.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    results = evaluate_cases(fixture)

    assert all(result.ok for result in results)
