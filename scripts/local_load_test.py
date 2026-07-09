from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.provider_clients import FakeHermesClient
from gateway_bot.service import GatewayService


def run_load_test(*, requests: int, concurrency: int, max_p95_ms: int) -> dict:
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(per_user_limit=requests + 1, global_limit=requests + 1),
    )
    service = GatewayService(pipeline)

    def invoke(index: int) -> tuple[int, str, str]:
        started = time.perf_counter()
        reply = service.handle_text(950_000_000 + index, f"/ask load probe {index}")
        return int((time.perf_counter() - started) * 1000), reply.trace_id, reply.blocked_reason

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        values = list(pool.map(invoke, range(requests)))
    latencies = sorted(value[0] for value in values)
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0
    failures = sum(bool(value[2]) or not value[1] for value in values)
    unique_trace_ids = len({value[1] for value in values if value[1]})
    return {
        "ok": failures == 0 and unique_trace_ids == requests and p95 <= max_p95_ms,
        "requests": requests,
        "concurrency": concurrency,
        "failures": failures,
        "unique_trace_ids": unique_trace_ids,
        "p50_ms": latencies[len(latencies) // 2] if latencies else 0,
        "p95_ms": p95,
        "max_ms": max(latencies, default=0),
        "total_ms": int((time.perf_counter() - started) * 1000),
        "max_p95_ms": max_p95_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded local GatewayService load probe.")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--max-p95-ms", type=int, default=1000)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        raise SystemExit("requests and concurrency must be positive")
    report = run_load_test(
        requests=args.requests,
        concurrency=args.concurrency,
        max_p95_ms=args.max_p95_ms,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
