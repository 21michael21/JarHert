#!/usr/bin/env bash
set -euo pipefail

cd /app
python scripts/run_migrations.py
exec python -m telegram_collector.app
