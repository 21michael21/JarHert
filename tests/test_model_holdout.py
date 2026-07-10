from assistant.model_holdout import (
    HoldoutCase,
    VariantSummary,
    action_router_safety_contract_failures,
    evaluate_candidate_gate,
    load_holdout_cases,
    score_holdout_answer,
)


def test_holdout_score_flags_unnecessary_question_and_fabricated_claim() -> None:
    case = HoldoutCase(
        id="unknown",
        prompt="Почему сервис упал? Логов нет.",
        category="insufficient_data",
        max_chars=200,
        required_any=("лог", "проверь"),
        factual_forbidden_patterns=(r"скорее всего",),
        allow_question=False,
    )

    result = score_holdout_answer(case, "Скорее всего упала база. Проверь логи?")

    assert result.factual_violation is True
    assert result.unnecessary_question is True
    assert result.quality_score < 90


def test_candidate_gate_requires_every_declared_threshold() -> None:
    base = VariantSummary(
        name="base",
        model="gpt-5-nano",
        case_count=100,
        quality_score=89,
        short_rate=0.88,
        unnecessary_question_rate=0.12,
        factual_violations=1,
        p50_latency_ms=1_000,
        p95_latency_ms=1_500,
        errors=0,
    )
    passing = VariantSummary(
        name="style",
        model="gpt-5-nano",
        case_count=100,
        quality_score=92,
        short_rate=0.93,
        unnecessary_question_rate=0.08,
        factual_violations=0,
        p50_latency_ms=1_180,
        p95_latency_ms=1_600,
        errors=0,
    )

    decision = evaluate_candidate_gate(base, passing, action_router_safety_failures=0)

    assert decision.passed is True
    assert decision.issues == ()

    slow = VariantSummary(**{**passing.__dict__, "p50_latency_ms": 1_201})
    failed = evaluate_candidate_gate(base, slow, action_router_safety_failures=1)

    assert failed.passed is False
    assert {"latency_regression", "action_router_safety_regression"} <= set(failed.issues)


def test_generated_closed_holdout_has_exactly_one_hundred_cases(tmp_path) -> None:
    from scripts.generate_model_holdout import write_holdout

    path = tmp_path / "holdout.json"
    write_holdout(path)
    cases = load_holdout_cases(path)

    assert len(cases) == 100
    assert len({case.id for case in cases}) == 100
    assert all(case.prompt and case.max_chars <= 420 for case in cases)


def test_action_router_and_safety_contracts_stay_green_for_holdout_gate() -> None:
    assert action_router_safety_contract_failures() == []
