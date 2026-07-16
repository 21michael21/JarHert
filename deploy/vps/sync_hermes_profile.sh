#!/usr/bin/env bash
set -Eeuo pipefail

# Synchronize only versioned JarHert Hermes profile assets. Runtime state,
# credentials, databases, sessions, logs, and the live provider config stay on the server.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE="${JARHERT_VPS:?set JARHERT_VPS=deploy@your-vps-host}"
REMOTE_SOURCE_DIR="${JARHERT_REMOTE_SOURCE_DIR:-/home/deploy/jarhert-profile}"
PROFILE_DIR="${HERMES_PROFILE_DIR:-/home/deploy/.hermes/profiles/jarhert}"
HERMES_PYTHON="${HERMES_PYTHON:-/home/deploy/.hermes/hermes-agent/venv/bin/python}"
GIT_URL="${JARHERT_GIT_URL:-https://github.com/21michael21/JarHert.git}"
SYNC_CONFIG="${SYNC_PROFILE_CONFIG:-merge}"

cd "$ROOT"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing to deploy from a dirty Git worktree." >&2
  exit 2
fi

git fetch origin main
LOCAL_COMMIT="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
if [[ "$LOCAL_COMMIT" != "$REMOTE_MAIN" ]]; then
  echo "Local HEAD must equal origin/main before profile sync." >&2
  exit 2
fi

ssh "$REMOTE" bash -s -- "$GIT_URL" "$LOCAL_COMMIT" "$REMOTE_SOURCE_DIR" "$PROFILE_DIR" "$HERMES_PYTHON" "$SYNC_CONFIG" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

GIT_URL="$1"
COMMIT="$2"
SOURCE_DIR="$3"
PROFILE_DIR="$4"
HERMES_PYTHON="$5"
SYNC_CONFIG="$6"
HERMES_SOURCE_DIR="${HERMES_SOURCE_DIR:-$(cd "$(dirname "$HERMES_PYTHON")/../.." && pwd)}"

[[ -d "$PROFILE_DIR" ]] || { echo "Hermes profile is missing: $PROFILE_DIR" >&2; exit 2; }
[[ -x "$HERMES_PYTHON" ]] || { echo "Hermes Python is missing: $HERMES_PYTHON" >&2; exit 2; }
[[ -f "$HERMES_SOURCE_DIR/pyproject.toml" ]] || { echo "Hermes source is missing: $HERMES_SOURCE_DIR" >&2; exit 2; }

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  git clone "$GIT_URL" "$SOURCE_DIR"
fi
git -C "$SOURCE_DIR" diff --quiet || {
  echo "Managed JarHert source is dirty: $SOURCE_DIR" >&2
  exit 2
}
git -C "$SOURCE_DIR" fetch --prune origin
git -C "$SOURCE_DIR" cat-file -e "${COMMIT}^{commit}"
git -C "$SOURCE_DIR" checkout --detach "$COMMIT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROLLBACK_DIR="$PROFILE_DIR/backups/profile-sync-$STAMP"
mkdir -p "$ROLLBACK_DIR"
for item in SOUL.md AGENTS.md config.yaml distribution.yaml requirements-native.txt skills native_tools scripts; do
  if [[ -e "$PROFILE_DIR/$item" ]]; then
    cp -a "$PROFILE_DIR/$item" "$ROLLBACK_DIR/"
  fi
done

rollback() {
  local status="$?"
  if [[ "$status" -ne 0 ]]; then
    echo "Profile sync failed; restoring versioned profile assets." >&2
    for item in SOUL.md AGENTS.md config.yaml distribution.yaml requirements-native.txt skills native_tools scripts; do
      if [[ -e "$ROLLBACK_DIR/$item" ]]; then
        rm -rf "$PROFILE_DIR/$item"
        cp -a "$ROLLBACK_DIR/$item" "$PROFILE_DIR/"
      fi
    done
    systemctl --user restart hermes-gateway-jarhert.service || true
  fi
  exit "$status"
}
trap rollback ERR

for item in SOUL.md AGENTS.md distribution.yaml requirements-native.txt; do
  cp -a "$SOURCE_DIR/hermes/$item" "$PROFILE_DIR/$item"
done
rsync -a "$SOURCE_DIR/hermes/skills/" "$PROFILE_DIR/skills/"
rsync -a "$SOURCE_DIR/hermes/native_tools/" "$PROFILE_DIR/native_tools/"
rsync -a "$SOURCE_DIR/hermes/scripts/" "$PROFILE_DIR/scripts/"

if [[ "$SYNC_CONFIG" == "1" ]]; then
  cp -a "$SOURCE_DIR/hermes/config.yaml" "$PROFILE_DIR/config.yaml"
elif [[ "$SYNC_CONFIG" == "merge" ]]; then
  "$HERMES_PYTHON" "$SOURCE_DIR/deploy/vps/merge_hermes_tools.py" "$SOURCE_DIR/hermes/config.yaml" "$PROFILE_DIR/config.yaml"
  echo "Merged safe JarHert defaults while preserving live config.yaml."
else
  echo "Preserved live config.yaml (set SYNC_PROFILE_CONFIG=1 to update it explicitly)."
fi

HERMES_HOME="$PROFILE_DIR" "$HERMES_PYTHON" "$PROFILE_DIR/scripts/bootstrap_native_deps.py"
# JarHert's native MCP is part of the running gateway, not the profile venv.
# Keep Hermes editable while aligning the MCP SDK with its pinned extra.
"$HERMES_PYTHON" -m pip install --editable "$HERMES_SOURCE_DIR[mcp]" >/dev/null
"$HERMES_PYTHON" "$SOURCE_DIR/deploy/vps/patch_hermes_interrupt_receipt.py" \
  "$HERMES_SOURCE_DIR/agent/conversation_loop.py"
"$HERMES_PYTHON" "$SOURCE_DIR/deploy/vps/patch_hermes_telegram_approval.py" \
  "$HERMES_SOURCE_DIR/plugins/platforms/telegram/adapter.py"
# This value is consumed both by a shell and Hermes' dotenv parser. Shell-style
# backslash escaping would make dotenv treat the complete command as one executable.
sed -i '/^HERMES_NATIVE_SEND_COMMAND=/d' "$PROFILE_DIR/.env"
printf "HERMES_NATIVE_SEND_COMMAND='%s -m hermes_cli.main'\n" "$HERMES_PYTHON" >> "$PROFILE_DIR/.env"
sed -i '/^HERMES_ACTION_PLAN_RECEIPT_DELIVERY=/d' "$PROFILE_DIR/.env"
printf 'HERMES_ACTION_PLAN_RECEIPT_DELIVERY=true\n' >> "$PROFILE_DIR/.env"
chmod 600 "$PROFILE_DIR/.env"
"$HERMES_PYTHON" -m hermes_cli.main --profile jarhert tools disable --platform telegram \
  terminal file code_execution browser computer_use delegation cronjob >/dev/null
install -Dm644 "$SOURCE_DIR/deploy/vps/systemd/hermes-gateway-jarhert.override.conf" \
  "$HOME/.config/systemd/user/hermes-gateway-jarhert.service.d/override.conf"
install -Dm644 "$SOURCE_DIR/deploy/vps/systemd/hermes-dashboard-jarhert.service" \
  "$HOME/.config/systemd/user/hermes-dashboard-jarhert.service"
systemctl --user daemon-reload
mkdir -p "$PROFILE_DIR/state"
printf '{"jarhert_commit":"%s","synced_at":"%s"}\n' "$COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$PROFILE_DIR/state/jarhert-profile-revision.json"
systemctl --user restart hermes-gateway-jarhert.service
systemctl --user is-active --quiet hermes-gateway-jarhert.service
if systemctl --user cat hermes-dashboard-jarhert.service >/dev/null 2>&1; then
  systemctl --user restart hermes-dashboard-jarhert.service
  systemctl --user is-active --quiet hermes-dashboard-jarhert.service
fi
"$HERMES_PYTHON" -m hermes_cli.main --profile jarhert skills list >/dev/null
echo "profile_sync=ok commit=$COMMIT rollback=$ROLLBACK_DIR"
REMOTE_SCRIPT

RETIRE_LEGACY_GATEWAY="${RETIRE_LEGACY_GATEWAY:-0}" \
LEGACY_GATEWAY_UNIT="${LEGACY_GATEWAY_UNIT:-}" \
JARHERT_VPS="$REMOTE" \
HERMES_PROFILE_DIR="$PROFILE_DIR" \
"$ROOT/deploy/vps/verify_single_telegram_gateway.sh"
