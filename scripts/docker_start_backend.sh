#!/usr/bin/env bash
set -euo pipefail

cd /app
python scripts/run_migrations.py
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
