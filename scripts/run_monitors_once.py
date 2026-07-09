#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.monitors.runner import run_monitors_once
from backend.stores import SqlDeliveryOutboxStore, SqlMonitorJobStore
from gateway_bot.main import build_hermes_client, get_session_factory
from scripts.run_migrations import run_migrations


def main() -> int:
    parser = argparse.ArgumentParser(description="Run enabled proactive monitors once.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum monitor jobs to check.")
    args = parser.parse_args()

    run_migrations()
    session_factory = get_session_factory()
    summary = run_monitors_once(
        monitor_jobs=SqlMonitorJobStore(session_factory),
        hermes=build_hermes_client(),
        delivery_outbox=SqlDeliveryOutboxStore(session_factory),
        limit=args.limit,
    )
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
