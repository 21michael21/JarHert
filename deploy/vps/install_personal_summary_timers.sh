#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE="${JARHERT_VPS:?set JARHERT_VPS=deploy@your-vps-host}"
REMOTE_UNITS_DIR="${JARHERT_SYSTEMD_USER_DIR:-/home/deploy/.config/systemd/user}"

units=(
  hermes-daily-brief.service
  hermes-daily-brief.timer
  hermes-weekly-review.service
  hermes-weekly-review.timer
  hermes-memory-consolidation.service
  hermes-memory-consolidation.timer
)

ssh "$REMOTE" "mkdir -p '$REMOTE_UNITS_DIR'"
for unit in "${units[@]}"; do
  scp "$ROOT/deploy/vps/systemd/$unit" "$REMOTE:$REMOTE_UNITS_DIR/"
done
ssh "$REMOTE" '
  systemctl --user daemon-reload
  systemctl --user enable --now hermes-daily-brief.timer hermes-weekly-review.timer hermes-memory-consolidation.timer
  systemctl --user is-active hermes-daily-brief.timer hermes-weekly-review.timer hermes-memory-consolidation.timer
'
