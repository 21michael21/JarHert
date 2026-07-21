#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/deploy/vps/require_personal_vps.sh"
require_personal_vps_local

PROFILE_DIR="${HERMES_PROFILE_DIR:-$HOME/.hermes/profiles/jarhert}"
ENV_FILE="$PROFILE_DIR/.env"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/hermes-dashboard-jarhert.service"

[[ -f "$ENV_FILE" ]] || { echo "Missing profile environment: $ENV_FILE" >&2; exit 2; }
grep -q '^JARHERT_DASHBOARD_SESSION_SECRET=.' "$ENV_FILE" || {
  echo "Set JARHERT_DASHBOARD_SESSION_SECRET in $ENV_FILE first." >&2
  exit 2
}

mkdir -p "$UNIT_DIR"
install -m 644 "$ROOT/deploy/vps/systemd/hermes-dashboard-jarhert.service" "$UNIT_FILE"
systemctl --user daemon-reload
systemctl --user enable --now hermes-dashboard-jarhert.service
systemctl --user --no-pager --full status hermes-dashboard-jarhert.service

echo "Dashboard listens on 127.0.0.1:8788. Publish it only through an HTTPS reverse proxy before setting the Telegram menu button."
