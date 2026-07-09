#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.automation_runtime import AutomationRuntime
from assistant.telegram_trends import TelegramTrendWorkerAdapter
from backend.automation_store import SqlAutomationLeaseStore
from backend.config import Settings
from backend.message_store import SqlCollectedMessageStore
from backend.stores import SqlDeliveryOutboxStore, UserStore
from gateway_bot.main import build_hermes_client, get_session_factory


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Telegram trendwatch worker.")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    tg_user_id = _target_tg_user_id(settings)
    session_factory = get_session_factory()
    user = UserStore(session_factory).get_or_create(tg_user_id)
    interval_seconds = float(os.getenv("TELEGRAM_TREND_INTERVAL_SECONDS", "3600"))
    adapter = TelegramTrendWorkerAdapter(
        SqlCollectedMessageStore(session_factory),
        build_hermes_client(),
        SqlDeliveryOutboxStore(session_factory),
        user_id=user.id,
        chat_id=tg_user_id,
        lookback_hours=int(os.getenv("TELEGRAM_TREND_LOOKBACK_HOURS", "6")),
        limit=int(os.getenv("TELEGRAM_TREND_BATCH_LIMIT", "300")),
        policy=replace(TelegramTrendWorkerAdapter.default_policy, interval_seconds=interval_seconds),
    )
    lease_store = SqlAutomationLeaseStore(session_factory)
    asyncio.run(
        AutomationRuntime(
            [adapter],
            lease_store,
            poll_seconds=min(5, max(0.1, interval_seconds)),
        ).run(stop_after_one_tick=args.once)
    )
    if args.once:
        lease = lease_store.get(adapter.name)
        if lease is not None and lease.status in {"retry_wait", "degraded"}:
            logging.error("trend_worker failed status=%s error=%s", lease.status, lease.last_error)
            return 1
    return 0


def _target_tg_user_id(settings: Settings) -> int:
    explicit = os.getenv("TELEGRAM_TREND_TG_USER_ID", "").strip()
    if explicit:
        return int(explicit)
    if settings.admin_tg_user_ids:
        return sorted(settings.admin_tg_user_ids)[0]
    raise RuntimeError("Set TELEGRAM_TREND_TG_USER_ID or ADMIN_TG_USER_IDS for trend delivery")


if __name__ == "__main__":
    raise SystemExit(main())
