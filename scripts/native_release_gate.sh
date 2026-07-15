#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/native_check.sh"

if [[ "${NATIVE_RELEASE_ALLOW_LIVE:-0}" != "1" ]]; then
  echo "live_native_telegram=skipped set NATIVE_RELEASE_ALLOW_LIVE=1 for the external proof"
  exit 0
fi

PROFILE_HOME="${HERMES_HOME:-$HOME/.hermes/profiles/jarhert}"
PYTHON="${NATIVE_RELEASE_PYTHON:-$PROFILE_HOME/.venv/bin/python}"
REPORT="${NATIVE_RELEASE_REPORT:-$ROOT/reports/native-release/live-hermes-e2e.json}"

[[ -x "$PYTHON" ]] || { echo "Native profile Python is missing: $PYTHON" >&2; exit 2; }
[[ -f "$PROFILE_HOME/.env" ]] || { echo "Native profile environment is missing: $PROFILE_HOME/.env" >&2; exit 2; }

HERMES_HOME="$PROFILE_HOME" "$PYTHON" "$ROOT/scripts/live_hermes_e2e.py" \
  --profile-home "$PROFILE_HOME" \
  --allow-live \
  --report "$REPORT"
echo "live_native_telegram=passed report=$REPORT"
