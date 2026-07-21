#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/deploy/vps/require_personal_vps.sh"
REMOTE="${JARHERT_VPS:-$JARHERT_PERSONAL_VPS_TARGET}"
REMOTE_UNITS_DIR="${JARHERT_SYSTEMD_USER_DIR:-/home/deploy/.config/systemd/user}"

require_personal_vps_remote "$REMOTE"
ssh "$REMOTE" "mkdir -p '$REMOTE_UNITS_DIR'"
scp \
  "$ROOT/deploy/vps/systemd/hermes-telegram-export-cleanup.service" \
  "$ROOT/deploy/vps/systemd/hermes-telegram-export-cleanup.timer" \
  "$REMOTE:$REMOTE_UNITS_DIR/"
ssh "$REMOTE" 'systemctl --user daemon-reload; systemctl --user enable --now hermes-telegram-export-cleanup.timer; systemctl --user is-active hermes-telegram-export-cleanup.timer'
