from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
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
    quality_reason: str
    error: str = ""
    preview: str = ""


@dataclass(frozen=True)
class ProviderBenchmarkResult:
    provider: str
    model: str
    cost_mode: str
    priority: int
    ok_count: int
    fail_count: int
    avg_latency_ms: int | None
    tasks: list[ProviderTaskResult]


def benchmark_provider(provider: ProviderSpec, settings: Settings) -> ProviderBenchmarkResult:
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
            tasks.append(
                ProviderTaskResult(
                    prompt_index=index,
                    ok=gate.ok,
                    latency_ms=response.latency_ms or elapsed_ms,
                    output_chars=len(response.text),
                    quality_reason=gate.reason or "ok",
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
                    quality_reason="exception",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    ok_latencies = [task.latency_ms for task in tasks if task.ok]
    return ProviderBenchmarkResult(
        provider=provider.name,
        model=provider.model,
        cost_mode=provider.cost_mode.value,
        priority=provider.priority,
        ok_count=sum(1 for task in tasks if task.ok),
        fail_count=sum(1 for task in tasks if not task.ok),
        avg_latency_ms=round(sum(ok_latencies) / len(ok_latencies)) if ok_latencies else None,
        tasks=tasks,
    )


def _preview(text: str, *, limit: int = 180) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark configured LLM providers on small assistant tasks.")
    parser.add_argument("--provider", action="append", help="Provider name to include. Can be repeated.")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()

    settings = Settings()
    registry = build_provider_registry(settings)
    providers = registry.enabled()
    if args.provider:
        wanted = set(args.provider)
        providers = [provider for provider in providers if provider.name in wanted]
    if not providers:
        print("provider_benchmark=fail no enabled providers")
        return 1

    results = [benchmark_provider(provider, settings) for provider in providers]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(result.fail_count == 0 for result in results),
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
        print(
            f"- {result.provider} {result.model}: "
            f"ok={result.ok_count} fail={result.fail_count} avg_latency_ms={result.avg_latency_ms}"
        )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
