#!/usr/bin/env bash

# JarHert is a personal service. Remote mutations must fail closed unless the
# target is the one pinned personal VPS and the server carries the role marker.

JARHERT_PERSONAL_VPS_TARGET="deploy@89.124.124.212"
JARHERT_PERSONAL_VPS_IP="89.124.124.212"
JARHERT_PERSONAL_VPS_HOSTNAME="jarhert"
JARHERT_PERSONAL_VPS_ROLE="jarhert-personal-vps-v1"
JARHERT_PERSONAL_VPS_ROLE_FILE="/home/deploy/.config/jarhert/server-role"
JARHERT_PERSONAL_VPS_ED25519_FINGERPRINT="SHA256:cwG4kRQQsY4OlMsRPSzn3Y1FuKVCEmkmvaQ6b0KPEBQ"

_personal_vps_fail() {
  printf 'JarHert personal VPS guard: %s\n' "$1" >&2
  return 2
}

_require_pinned_personal_target() {
  local remote="$1"
  if [[ "$remote" != "$JARHERT_PERSONAL_VPS_TARGET" ]]; then
    _personal_vps_fail "Refusing JarHert operation on unpinned VPS: $remote"
    return 2
  fi
}

_verify_personal_vps_host_key() {
  local scanned_key fingerprint
  scanned_key="$(ssh-keyscan -T 5 -t ed25519 "$JARHERT_PERSONAL_VPS_IP" 2>/dev/null)" || {
    _personal_vps_fail "could not read the pinned VPS SSH host key"
    return 2
  }
  [[ -n "$scanned_key" ]] || {
    _personal_vps_fail "the pinned VPS returned no ED25519 host key"
    return 2
  }
  fingerprint="$(printf '%s\n' "$scanned_key" | ssh-keygen -lf - -E sha256 | awk 'NR == 1 {print $2}')"
  if [[ "$fingerprint" != "$JARHERT_PERSONAL_VPS_ED25519_FINGERPRINT" ]]; then
    _personal_vps_fail "SSH host key mismatch for $JARHERT_PERSONAL_VPS_IP"
    return 2
  fi
}

_read_personal_vps_identity() {
  local remote="$1"
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$remote" \
    "ROLE_FILE='$JARHERT_PERSONAL_VPS_ROLE_FILE' bash -s" <<'REMOTE_IDENTITY'
set -Eeuo pipefail
printf 'host=%s\n' "$(hostname)"
printf 'ip=%s\n' "$(ip -o -4 addr show scope global | awk 'NR == 1 {split($4, address, "/"); print address[1]}')"
if [[ -r "$ROLE_FILE" ]]; then
  printf 'role=%s\n' "$(cat "$ROLE_FILE")"
else
  printf 'role=missing\n'
fi
REMOTE_IDENTITY
}

verify_personal_vps_endpoint() {
  local remote="$1" identity remote_host remote_ip
  _require_pinned_personal_target "$remote" || return 2
  _verify_personal_vps_host_key || return 2
  identity="$(_read_personal_vps_identity "$remote")" || {
    _personal_vps_fail "could not verify the pinned VPS identity"
    return 2
  }
  remote_host="$(printf '%s\n' "$identity" | sed -n 's/^host=//p')"
  remote_ip="$(printf '%s\n' "$identity" | sed -n 's/^ip=//p')"
  if [[ "$remote_host" != "$JARHERT_PERSONAL_VPS_HOSTNAME" ]]; then
    _personal_vps_fail "hostname mismatch: expected $JARHERT_PERSONAL_VPS_HOSTNAME, got ${remote_host:-missing}"
    return 2
  fi
  if [[ "$remote_ip" != "$JARHERT_PERSONAL_VPS_IP" ]]; then
    _personal_vps_fail "IP mismatch: expected $JARHERT_PERSONAL_VPS_IP, got ${remote_ip:-missing}"
    return 2
  fi
  JARHERT_VERIFIED_REMOTE_IDENTITY="$identity"
}

require_personal_vps_remote() {
  local remote="$1" remote_role
  verify_personal_vps_endpoint "$remote" || return 2
  remote_role="$(printf '%s\n' "$JARHERT_VERIFIED_REMOTE_IDENTITY" | sed -n 's/^role=//p')"
  if [[ "$remote_role" != "$JARHERT_PERSONAL_VPS_ROLE" ]]; then
    _personal_vps_fail "role marker mismatch: expected $JARHERT_PERSONAL_VPS_ROLE, got ${remote_role:-missing}"
    return 2
  fi
  echo "personal_vps_guard=ok"
}

require_personal_vps_local() {
  local local_host local_ip local_role
  local_host="$(hostname)"
  local_ip="$(ip -o -4 addr show scope global | awk 'NR == 1 {split($4, address, "/"); print address[1]}')"
  local_role="missing"
  if [[ -r "$JARHERT_PERSONAL_VPS_ROLE_FILE" ]]; then
    local_role="$(cat "$JARHERT_PERSONAL_VPS_ROLE_FILE")"
  fi
  if [[ "$local_host" != "$JARHERT_PERSONAL_VPS_HOSTNAME" || \
        "$local_ip" != "$JARHERT_PERSONAL_VPS_IP" || \
        "$local_role" != "$JARHERT_PERSONAL_VPS_ROLE" ]]; then
    _personal_vps_fail "local server identity mismatch (host=$local_host ip=$local_ip role=$local_role)"
    return 2
  fi
  echo "personal_vps_guard=ok"
}
