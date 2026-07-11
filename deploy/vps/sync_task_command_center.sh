#!/usr/bin/env bash
set -Eeuo pipefail

# Task Command Center is intentionally an external prerequisite. This script
# copies its runtime code and credentials directly to the VPS; no secret enters
# the JarHert repository or Hermes profile distribution.

SOURCE_DIR="${TASK_COMMAND_CENTER_SOURCE:?set TASK_COMMAND_CENTER_SOURCE=/absolute/path/to/task-command-center}"
REMOTE="${JARHERT_VPS:?set JARHERT_VPS=deploy@your-vps-host}"
REMOTE_DIR="${TASK_COMMAND_CENTER_REMOTE_DIR:-/home/deploy/task-command-center}"
PROFILE_ENV="${HERMES_PROFILE_ENV:-/home/deploy/.hermes/profiles/jarhert/.env}"
COPY_SECRETS="${TASK_COMMAND_CENTER_COPY_SECRETS:-0}"

if [[ "$COPY_SECRETS" != "1" ]]; then
  echo "Refusing to copy Task Command Center credentials. Set TASK_COMMAND_CENTER_COPY_SECRETS=1 explicitly." >&2
  exit 2
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
for item in taskctl.py requirements.txt config.yaml .env client_secret.json token.json; do
  if [[ ! -f "$SOURCE_DIR/$item" ]]; then
    echo "Task Command Center source is missing required file: $item" >&2
    exit 2
  fi
done

ssh "$REMOTE" "mkdir -p '$REMOTE_DIR'"
rsync -a --delete --exclude '.env' --exclude '.venv/' --exclude '.git/' --exclude '.pytest_cache/' --exclude '__pycache__/' --exclude '.taskctl_mock/' --exclude 'client_secret.json' --exclude 'token.json*' "$SOURCE_DIR/" "$REMOTE:$REMOTE_DIR/"
rsync -a "$SOURCE_DIR/.env" "$SOURCE_DIR/client_secret.json" "$SOURCE_DIR/token.json" "$REMOTE:$REMOTE_DIR/"

ssh "$REMOTE" bash -s -- "$REMOTE_DIR" "$PROFILE_ENV" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

REMOTE_DIR="$1"
PROFILE_ENV="$2"
python3 -m venv "$REMOTE_DIR/.venv"
"$REMOTE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$REMOTE_DIR/requirements.txt"
chmod 700 "$REMOTE_DIR"
chmod 600 "$REMOTE_DIR/.env" "$REMOTE_DIR/client_secret.json" "$REMOTE_DIR/token.json"

python3 - "$PROFILE_ENV" "$REMOTE_DIR" <<'PYTHON_SCRIPT'
from pathlib import Path
import sys

path = Path(sys.argv[1])
target = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
replacement = {
    "TASK_COMMAND_CENTER_DIR": target,
    "TASK_COMMAND_CENTER_PYTHON": ".venv/bin/python",
}
seen = set()
updated = []
for line in lines:
    key, separator, _value = line.partition("=")
    if separator and key in replacement:
        updated.append(f"{key}={replacement[key]}")
        seen.add(key)
    else:
        updated.append(line)
for key, value in replacement.items():
    if key not in seen:
        updated.append(f"{key}={value}")
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PYTHON_SCRIPT

"$REMOTE_DIR/.venv/bin/python" "$REMOTE_DIR/taskctl.py" list --list Today >/dev/null
"$REMOTE_DIR/.venv/bin/python" -c "from pathlib import Path; from src.config import load_config; from src.google_calendar_client import GoogleCalendarClient; config=load_config(Path('.')); client=GoogleCalendarClient(config, Path('.')); client.validate_setup(); client.list_today_events()" >/dev/null
systemctl --user restart hermes-gateway-jarhert.service
systemctl --user is-active --quiet hermes-gateway-jarhert.service
PROFILE_DIR="$(dirname "$PROFILE_ENV")"
set -a
. "$PROFILE_ENV"
set +a
"$PROFILE_DIR/.venv/bin/python" "$PROFILE_DIR/native_tools/cli.py" integration-health
echo "task_command_center_sync=ok"
REMOTE_SCRIPT
