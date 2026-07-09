#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
  PYTHON_BIN=.venv/bin/python
else
  PYTHON_BIN=python
fi

"$PYTHON_BIN" scripts/run_migrations.py
