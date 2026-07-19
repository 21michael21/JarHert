#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${NATIVE_CHECK_PYTHON:-$ROOT/.venv/bin/python}"

cd "$ROOT"
"$PYTHON" -m pytest -q tests/
"$PYTHON" -m compileall hermes/native_tools hermes/scripts deploy/vps
"$PYTHON" scripts/security_scan.py
