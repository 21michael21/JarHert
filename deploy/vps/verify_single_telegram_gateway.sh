#!/usr/bin/env bash
set -Eeo pipefail

REMOTE=$JARHERT_VPS
if [[ -z "$REMOTE" ]]; then
  echo "Set JARHERT_VPS=deploy@your-vps-host." >&2
  exit 2
fi
PROFILE_DIR=/home/deploy/.hermes/profiles/jarhert
if [[ -n "$HERMES_PROFILE_DIR" ]]; then
  PROFILE_DIR=$HERMES_PROFILE_DIR
fi
RETIRE=$RETIRE_LEGACY_GATEWAY
LEGACY_UNIT=$LEGACY_GATEWAY_UNIT

if [[ "$RETIRE" == "1" && ! "$LEGACY_UNIT" =~ ^[A-Za-z0-9@._-]+\.service$ ]]; then
  echo "Set LEGACY_GATEWAY_UNIT=<exact-user-systemd-unit.service> with RETIRE_LEGACY_GATEWAY=1." >&2
  exit 2
fi

ssh "$REMOTE" "PROFILE_DIR='$PROFILE_DIR' RETIRE='$RETIRE' LEGACY_UNIT='$LEGACY_UNIT' bash -s" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

if [[ "$RETIRE" == "1" ]]; then
  systemctl --user disable --now "$LEGACY_UNIT"
  echo "legacy_unit_retired=$LEGACY_UNIT"
fi

hermes_state="$(systemctl --user is-active hermes-gateway-jarhert.service || true)"
legacy_processes="$(pgrep -af '[g]ateway_bot[.]telegram_app' || true)"
legacy_containers=""
if command -v docker >/dev/null 2>&1; then
  legacy_containers="$(docker ps --format '{{.ID}} {{.Names}} {{.Command}}' 2>/dev/null | grep -E 'gateway_bot[.]telegram_app|telegram-ai-brooch' || true)"
fi

printf 'hermes_gateway=%s\n' "$hermes_state"
if [[ -n "$legacy_processes" || -n "$legacy_containers" ]]; then
  echo "legacy_gateway_detected=true" >&2
  [[ -z "$legacy_processes" ]] || printf '%s\n' "$legacy_processes" >&2
  [[ -z "$legacy_containers" ]] || printf '%s\n' "$legacy_containers" >&2
  echo "Stop the exact legacy service or container, then rerun this check." >&2
  exit 1
fi
if [[ "$hermes_state" != "active" ]]; then
  echo "Hermes gateway is not active." >&2
  exit 1
fi
echo "single_gateway_ok=true"
REMOTE_SCRIPT
