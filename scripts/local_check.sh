#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake .venv/bin/python -m pytest -q
.venv/bin/python -m compileall assistant backend gateway_bot reminders
ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake .venv/bin/python scripts/eval_golden.py
ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake .venv/bin/python scripts/hermes_adapter_smoke.py
