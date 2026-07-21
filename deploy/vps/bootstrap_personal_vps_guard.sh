#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/require_personal_vps.sh"

REMOTE="${JARHERT_VPS:-$JARHERT_PERSONAL_VPS_TARGET}"
verify_personal_vps_endpoint "$REMOTE"

ssh "$REMOTE" "ROLE_FILE='$JARHERT_PERSONAL_VPS_ROLE_FILE' ROLE='$JARHERT_PERSONAL_VPS_ROLE' bash -s" <<'REMOTE_SCRIPT'
set -Eeuo pipefail
install -d -m 700 "$(dirname "$ROLE_FILE")"
printf '%s\n' "$ROLE" >"$ROLE_FILE"
chmod 600 "$ROLE_FILE"
REMOTE_SCRIPT

require_personal_vps_remote "$REMOTE"
echo "personal_vps_guard_bootstrapped=true"
