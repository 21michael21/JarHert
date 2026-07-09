#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill BOT_TOKEN, then run again." >&2
  exit 1
fi

.venv/bin/python - <<'PY'
from backend.config import Settings

if not Settings().bot_token:
    raise SystemExit("BOT_TOKEN is empty in .env")
PY

.venv/bin/python scripts/init_db.py
.venv/bin/python -m gateway_bot.telegram_app
