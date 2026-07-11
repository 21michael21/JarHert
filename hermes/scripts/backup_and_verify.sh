#!/usr/bin/env bash
set -Eeuo pipefail

PROFILE_HOME="${HERMES_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${HERMES_BACKUP_DIR:-$HOME/.hermes/backups/jarhert}"
PYTHON_BIN="${PROFILE_HOME}/.venv/bin/python"

result="$("$PYTHON_BIN" "$PROFILE_HOME/scripts/backup_profile.py" --profile-home "$PROFILE_HOME" --backup-dir "$BACKUP_DIR" backup)"
archive="$(printf '%s' "$result" | "$PYTHON_BIN" -c 'import json, sys; print(json.load(sys.stdin)["archive"])')"
"$PYTHON_BIN" "$PROFILE_HOME/scripts/backup_profile.py" --profile-home "$PROFILE_HOME" --backup-dir "$BACKUP_DIR" verify --archive "$archive"
