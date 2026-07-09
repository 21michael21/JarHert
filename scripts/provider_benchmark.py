from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.provider_registry import ProviderSpec, build_provider_registry
from assistant.quality_gates import check_output
from assistant.types import HermesRequest, UserContext
from backend.config import Settings
from gateway_bot.main import build_provider_client


DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "provider_benchmarks"

BENCHMARK_PROMPTS = [
    "Ответь одним коротким предложением: что такое MVP?",
    "Сожми мысль до 120 символов: нужно сделать ассистента быстрее и надежнее.",
    "Верни JSON: {\"action\":\"reminder.create\",\"text\":\"позвонить\",\"when\":\"tomorrow 09:00\"}",
    "Коротко объясни, почему нельзя хранить токены в коде.",
    "Составь 3 пункта проверки перед деплоем.",
    "Исправь текст без канцелярита: Данный сервис является помощником.",
    "Выдели намерение: завтра в 10 проверить сервер.",
    "Напиши короткий ответ пользователю, если провайдер недоступен.",
    "Сделай фразу дружелюбнее: ошибка выполнения задачи.",
    "Назови один риск бесплатных LLM API.",
]


@dataclass(frozen=True)
class ProviderTaskResult:
    prompt_index: int
    ok: bool
    latency_ms: int
    output_chars: int
    quality_score: int
    quality_reason: str
    error: str = ""
    preview: str = ""


@dataclass(frozen=True)
class ProviderBenchmarkResult:
    provider: str
    model: str
    cost_mode: str
    priority: int
    total_count: int
    ok_count: int
    fail_count: int
    fail_rate: float
    success_rate: float
    quality_score: int
    avg_latency_ms: int | None
    p95_latency_ms: int | None
    max_latency_ms: int | None
    gate_pass: bool
    tasks: list[ProviderTaskResult]
    gate_issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkThresholds:
    max_fail_rate: float = 0.20
    min_quality_score: int = 75
    max_avg_latency_ms: int = 12_000
    max_p95_latency_ms: int = 20_000


def benchmark_provider(
    provider: ProviderSpec,
    settings: Settings,
    *,
    thresholds: BenchmarkThresholds,
) -> ProviderBenchmarkResult:
    client = build_provider_client(provider, settings)
    tasks: list[ProviderTaskResult] = []
    for index, prompt in enumerate(BENCHMARK_PROMPTS, start=1):
        started = time.perf_counter()
        try:
            response = client.ask(
                HermesRequest(
                    user=UserContext(user_id=0, tg_user_id=0),
                    prompt=prompt,
                )
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            gate = check_output(response.text)
            quality_score, quality_reason = score_response_quality(prompt, response.text, gate.ok, gate.reason)
            tasks.append(
                ProviderTaskResult(
                    prompt_index=index,
                    ok=quality_score >= 60,
                    latency_ms=response.latency_ms or elapsed_ms,
                    output_chars=len(response.text),
                    quality_score=quality_score,
                    quality_reason=quality_reason,
                    preview=_preview(gate.safe_text or response.text),
                )
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            tasks.append(
                ProviderTaskResult(
                    prompt_index=index,
                    ok=False,
                    latency_ms=elapsed_ms,
                    output_chars=0,
                    quality_score=0,
                    quality_reason="exception",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    return summarize_provider_result(provider, tasks, thresholds=thresholds)


def score_response_quality(prompt: str, text: str, gate_ok: bool, gate_reason: str = "") -> tuple[int, str]:
    if not gate_ok:
        return 0, gate_reason or "quality_gate_failed"
    clean = " ".join((text or "").split())
    if len(clean) < 3:
        return 30, "too_short"
    if _expects_json(prompt) and not _extract_json_object(clean):
        return 50, "json_invalid"
    return 100, "ok"


def summarize_provider_result(
    provider: ProviderSpec,
    tasks: list[ProviderTaskResult],
    *,
    thresholds: BenchmarkThresholds,
) -> ProviderBenchmarkResult:
    total_count = len(tasks)
    ok_count = sum(1 for task in tasks if task.ok)
    fail_count = total_count - ok_count
    latencies = [task.latency_ms for task in tasks]
    quality_score = round(sum(task.quality_score for task in tasks) / total_count) if total_count else 0
    fail_rate = round(fail_count / total_count, 4) if total_count else 1.0
    success_rate = round(ok_count / total_count, 4) if total_count else 0.0
    avg_latency_ms = round(sum(latencies) / len(latencies)) if latencies else None
    p95_latency_ms = _percentile_nearest(latencies, 95)
    max_latency_ms = max(latencies) if latencies else None
    gate_issues = evaluate_gate(
        fail_rate=fail_rate,
        quality_score=quality_score,
        avg_latency_ms=avg_latency_ms,
        p95_latency_ms=p95_latency_ms,
        thresholds=thresholds,
    )
    return ProviderBenchmarkResult(
        provider=provider.name,
        model=provider.model,
        cost_mode=provider.cost_mode.value,
        priority=provider.priority,
        total_count=total_count,
        ok_count=ok_count,
        fail_count=fail_count,
        fail_rate=fail_rate,
        success_rate=success_rate,
        quality_score=quality_score,
        avg_latency_ms=avg_latency_ms,
        p95_latency_ms=p95_latency_ms,
        max_latency_ms=max_latency_ms,
        gate_pass=not gate_issues,
        gate_issues=gate_issues,
        tasks=tasks,
    )


def evaluate_gate(
    *,
    fail_rate: float,
    quality_score: int,
    avg_latency_ms: int | None,
    p95_latency_ms: int | None,
    thresholds: BenchmarkThresholds,
) -> list[str]:
    issues: list[str] = []
    if fail_rate > thresholds.max_fail_rate:
        issues.append(f"fail_rate {fail_rate:.0%} > {thresholds.max_fail_rate:.0%}")
    if quality_score < thresholds.min_quality_score:
        issues.append(f"quality_score {quality_score} < {thresholds.min_quality_score}")
    if avg_latency_ms is None:
        issues.append("avg_latency_ms missing")
    elif avg_latency_ms > thresholds.max_avg_latency_ms:
        issues.append(f"avg_latency_ms {avg_latency_ms} > {thresholds.max_avg_latency_ms}")
    if p95_latency_ms is None:
        issues.append("p95_latency_ms missing")
    elif p95_latency_ms > thresholds.max_p95_latency_ms:
        issues.append(f"p95_latency_ms {p95_latency_ms} > {thresholds.max_p95_latency_ms}")
    return issues


def _preview(text: str, *, limit: int = 180) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _expects_json(prompt: str) -> bool:
    return "json" in prompt.lower()


def _extract_json_object(text: str) -> dict | None:
    value = text.strip()
    if value.startswith("```"):
        parts = value.split("```")
        if len(parts) >= 3:
            value = parts[1].removeprefix("json").strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _percentile_nearest(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round((percentile / 100) * len(ordered)))
    return ordered[min(len(ordered) - 1, rank - 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark configured LLM providers on small assistant tasks.")
    parser.add_argument("--provider", action="append", help="Provider name to include. Can be repeated.")
    parser.add_argument(
        "--cost-mode",
        action="append",
        choices=["free", "cheap", "local", "paid"],
        help="Only include providers with this cost mode. Can be repeated.",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--gate", action="store_true", help="Fail when any provider misses benchmark thresholds.")
    parser.add_argument("--max-fail-rate", type=float, default=0.20)
    parser.add_argument("--min-quality-score", type=int, default=75)
    parser.add_argument("--max-avg-latency-ms", type=int, default=12_000)
    parser.add_argument("--max-p95-latency-ms", type=int, default=20_000)
    parser.add_argument(
        "--min-passing-providers",
        type=int,
        help="Gate passes when at least this many selected providers pass. Default: all selected providers.",
    )
    parser.add_argument("--allow-empty", action="store_true", help="Exit 0 when selected provider set is empty.")
    args = parser.parse_args()

    thresholds = BenchmarkThresholds(
        max_fail_rate=args.max_fail_rate,
        min_quality_score=args.min_quality_score,
        max_avg_latency_ms=args.max_avg_latency_ms,
        max_p95_latency_ms=args.max_p95_latency_ms,
    )
    settings = Settings()
    registry = build_provider_registry(settings)
    providers = registry.enabled()
    if args.provider:
        wanted = set(args.provider)
        providers = [provider for provider in providers if provider.name in wanted]
    if args.cost_mode:
        wanted_cost_modes = set(args.cost_mode)
        providers = [provider for provider in providers if provider.cost_mode.value in wanted_cost_modes]
    if not providers:
        if args.allow_empty:
            print("provider_benchmark=skip no enabled providers")
            return 0
        print("provider_benchmark=fail no enabled providers")
        return 1

    results = [benchmark_provider(provider, settings, thresholds=thresholds) for provider in providers]
    passing_providers = sum(1 for result in results if result.gate_pass)
    required_passing = args.min_passing_providers if args.min_passing_providers is not None else len(results)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "gate" if args.gate else "report",
        "thresholds": asdict(thresholds),
        "required_passing_providers": required_passing if args.gate else None,
        "passing_providers": passing_providers,
        "ok": passing_providers >= required_passing if args.gate else all(result.ok_count > 0 for result in results),
        "results": [
            {
                **asdict(result),
                "tasks": [asdict(task) for task in result.tasks],
            }
            for result in results
        ],
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"provider_benchmark report={report_path}")
    for result in results:
        gate = "pass" if result.gate_pass else "fail"
        print(
            f"- {result.provider} {result.model}: "
            f"ok={result.ok_count}/{result.total_count} "
            f"fail_rate={result.fail_rate:.0%} "
            f"quality={result.quality_score} "
            f"avg_latency_ms={result.avg_latency_ms} "
            f"p95_latency_ms={result.p95_latency_ms} "
            f"gate={gate}"
        )
        if result.gate_issues:
            for issue in result.gate_issues:
                print(f"  - {issue}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
