#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

HERMES_MODE=fake .venv/bin/python -m pytest -q
.venv/bin/python -m compileall assistant backend gateway_bot reminders
HERMES_MODE=fake .venv/bin/python scripts/hermes_adapter_smoke.py
