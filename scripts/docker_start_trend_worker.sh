#!/usr/bin/env bash
set -euo pipefail

cd /app
exec python scripts/run_telegram_trend_worker.py
