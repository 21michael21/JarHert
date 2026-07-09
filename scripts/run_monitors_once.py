#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.automation_runtime import AutomationRuntime
from assistant.monitors.runner import MonitorWorkerAdapter
from backend.automation_store import SqlAutomationLeaseStore
from backend.stores import SqlDeliveryOutboxStore, SqlMonitorJobStore
from gateway_bot.main import build_hermes_client, get_session_factory


def main() -> int:
    parser = argparse.ArgumentParser(description="Run enabled proactive monitors once.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum monitor jobs to check.")
    args = parser.parse_args()

    session_factory = get_session_factory()
    adapter = MonitorWorkerAdapter(
        monitor_jobs=SqlMonitorJobStore(session_factory),
        hermes=build_hermes_client(),
        delivery_outbox=SqlDeliveryOutboxStore(session_factory),
        limit=args.limit,
        policy=replace(MonitorWorkerAdapter.default_policy, interval_seconds=0),
    )
    lease_store = SqlAutomationLeaseStore(session_factory)
    asyncio.run(
        AutomationRuntime(
            [adapter],
            lease_store,
        ).run(stop_after_one_tick=True)
    )
    summary = adapter.last_result
    if summary is None:
        lease = lease_store.get(adapter.name)
        if lease is not None and lease.status in {"retry_wait", "degraded"}:
            print(f"monitor_run failed={lease.status} error={lease.last_error or 'unknown'}")
            return 1
        print("monitor_run skipped=lease_busy")
        return 0
    print(
        "monitor_run "
        + " ".join(
            f"{key}={summary[key]}"
            for key in ["checked", "no_change", "triggered", "not_triggered", "failed"]
        )
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
