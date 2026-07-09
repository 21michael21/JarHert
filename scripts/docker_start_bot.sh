#!/usr/bin/env bash
set -euo pipefail

cd /app
python scripts/run_migrations.py
exec python -m gateway_bot.telegram_app
