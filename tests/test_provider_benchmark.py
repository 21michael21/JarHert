from assistant.provider_registry import ProviderCostMode, ProviderKind, ProviderSpec
from scripts.provider_benchmark import (
    BenchmarkThresholds,
    ProviderTaskResult,
    score_response_quality,
    summarize_provider_result,
)


def provider() -> ProviderSpec:
    return ProviderSpec(
        name="openrouter_free",
        model="openrouter/free",
        cost_mode=ProviderCostMode.FREE,
        timeout_seconds=12,
        max_tokens=500,
        supports_json=True,
        priority=10,
        kind=ProviderKind.OPENAI_CHAT,
    )


def task(ok: bool, latency_ms: int, quality_score: int = 100) -> ProviderTaskResult:
    return ProviderTaskResult(
        prompt_index=1,
        ok=ok,
        latency_ms=latency_ms,
        output_chars=20 if ok else 0,
        quality_score=quality_score,
        quality_reason="ok" if ok else "exception",
    )


def test_provider_quality_scores_json_task() -> None:
    assert score_response_quality('Верни JSON: {"a":1}', '{"a": 1}', True) == (100, "ok")
    assert score_response_quality('Верни JSON: {"a":1}', "не json", True) == (50, "json_invalid")
    assert score_response_quality("Ответь коротко", "Я как ИИ", False, "ai_slop_marker") == (
        0,
        "ai_slop_marker",
    )


def test_provider_benchmark_summary_passes_gate() -> None:
    result = summarize_provider_result(
        provider(),
        [task(True, 1000), task(True, 2000), task(True, 3000), task(False, 4000, 0)],
        thresholds=BenchmarkThresholds(
            max_fail_rate=0.25,
            min_quality_score=70,
            max_avg_latency_ms=4000,
            max_p95_latency_ms=5000,
        ),
    )

    assert result.gate_pass
    assert result.ok_count == 3
    assert result.fail_rate == 0.25
    assert result.quality_score == 75
    assert result.avg_latency_ms == 2500
    assert result.p95_latency_ms == 4000


def test_provider_benchmark_summary_fails_gate_with_reasons() -> None:
    result = summarize_provider_result(
        provider(),
        [task(False, 20_000, 0), task(False, 25_000, 0), task(True, 30_000, 50)],
        thresholds=BenchmarkThresholds(
            max_fail_rate=0.20,
            min_quality_score=75,
            max_avg_latency_ms=12_000,
            max_p95_latency_ms=20_000,
        ),
    )

    assert not result.gate_pass
    assert any("fail_rate" in issue for issue in result.gate_issues)
    assert any("quality_score" in issue for issue in result.gate_issues)
    assert any("avg_latency_ms" in issue for issue in result.gate_issues)
    assert any("p95_latency_ms" in issue for issue in result.gate_issues)
