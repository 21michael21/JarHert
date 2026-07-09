#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

scripts/migrate.sh
ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake .venv/bin/python -m pytest -q
.venv/bin/python -m compileall assistant backend gateway_bot reminders telegram_collector
ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake .venv/bin/python scripts/eval_golden.py
ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake .venv/bin/python scripts/hermes_adapter_smoke.py
if [[ "${RUN_PROVIDER_BENCHMARK_GATE:-0}" == "1" ]]; then
  scripts/provider_quality_gate.sh
else
  echo "provider_quality_gate=skipped set RUN_PROVIDER_BENCHMARK_GATE=1"
fi
