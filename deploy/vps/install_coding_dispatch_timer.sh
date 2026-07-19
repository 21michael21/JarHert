#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE="${JARHERT_VPS:?set JARHERT_VPS=deploy@your-vps-host}"
REMOTE_UNITS_DIR="${JARHERT_SYSTEMD_USER_DIR:-/home/deploy/.config/systemd/user}"

ssh "$REMOTE" "mkdir -p '$REMOTE_UNITS_DIR'"
scp "$ROOT/deploy/vps/systemd/hermes-coding-dispatch.service" "$ROOT/deploy/vps/systemd/hermes-coding-dispatch.timer" "$REMOTE:$REMOTE_UNITS_DIR/"
ssh "$REMOTE" 'systemctl --user daemon-reload; systemctl --user enable --now hermes-coding-dispatch.timer; systemctl --user start hermes-coding-dispatch.service; systemctl --user show hermes-coding-dispatch.service -p Result -p ExecMainStatus; systemctl --user is-active hermes-coding-dispatch.timer'
