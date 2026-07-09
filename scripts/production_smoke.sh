#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TG_USER_ID="${TG_USER_ID:-${ADMIN_TG_USER_ID:-}}"
SERVICE_TOKEN="${ASSISTANT_SERVICE_TOKEN:-}"
SEND_TELEGRAM="${SEND_TELEGRAM:-0}"

cd "$(dirname "$0")/.."

need() {
  if [[ -z "${!1:-}" ]]; then
    echo "missing required env: $1" >&2
    exit 1
  fi
}

check_json_field() {
  local body="$1"
  local field="$2"
  local expected="$3"
  BODY="$body" FIELD="$field" EXPECTED="$expected" .venv/bin/python - <<'PY'
import json
import os
body = json.loads(os.environ["BODY"])
field = os.environ["FIELD"]
expected = os.environ["EXPECTED"]
actual = str(body.get(field, ""))
if actual != expected:
    raise SystemExit(f"{field}: expected={expected!r} got={actual!r}")
PY
}

echo "production_smoke base_url=${BASE_URL}"

health="$(curl -fsS "${BASE_URL}/health")"
check_json_field "$health" status ok
echo "health=ok ${health}"

version="$(curl -fsS "${BASE_URL}/api/version")"
check_json_field "$version" status ok
echo "version=ok ${version}"

status_code="$(curl -sS -o /tmp/jarhert_unauth_smoke.out -w "%{http_code}" \
  -X POST "${BASE_URL}/api/assistant/telegram-text" \
  -H "Content-Type: application/json" \
  --data '{"tg_user_id":1,"text":"/status"}')"
if [[ "$status_code" != "401" ]]; then
  echo "auth_check=fail expected=401 got=${status_code}" >&2
  cat /tmp/jarhert_unauth_smoke.out >&2 || true
  exit 1
fi
echo "auth_check=ok 401"

if [[ -n "$SERVICE_TOKEN" && -n "$TG_USER_ID" ]]; then
  admin_status="$(curl -fsS \
    -X POST "${BASE_URL}/api/assistant/telegram-text" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${SERVICE_TOKEN}" \
    --data "{\"tg_user_id\":${TG_USER_ID},\"text\":\"/admin_status\"}")"
  ADMIN_STATUS="$admin_status" .venv/bin/python - <<'PY'
import json
import os
payload = json.loads(os.environ["ADMIN_STATUS"])
text = payload.get("text") or ""
required = ["Admin status", "Providers:", "Task Center:", "trello=", "calendar="]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"admin_status missing: {missing}; text={text!r}")
PY
  echo "admin_status=ok"

  provider_reply="$(curl -fsS \
    -X POST "${BASE_URL}/api/assistant/telegram-text" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${SERVICE_TOKEN}" \
    --data "{\"tg_user_id\":${TG_USER_ID},\"text\":\"/ask ответь одним словом: ok\"}")"
  PROVIDER_REPLY="$provider_reply" .venv/bin/python - <<'PY'
import json
import os
payload = json.loads(os.environ["PROVIDER_REPLY"])
if payload.get("blocked_reason"):
    raise SystemExit(f"provider blocked: {payload.get('blocked_reason')}")
if not (payload.get("provider") or payload.get("model")):
    raise SystemExit(f"provider metadata missing: {payload}")
PY
  echo "provider=ok"
else
  echo "admin_status=skipped set ASSISTANT_SERVICE_TOKEN and TG_USER_ID"
  echo "provider=skipped set ASSISTANT_SERVICE_TOKEN and TG_USER_ID"
fi

if [[ "$SEND_TELEGRAM" == "1" ]]; then
  need TG_USER_ID
  .venv/bin/python scripts/live_e2e.py \
    --tg-user-id "$TG_USER_ID" \
    --send-telegram \
    --require-real-llm \
    --include-task \
    --include-calendar
  echo "telegram_delivery=ok"
else
  echo "telegram_delivery=skipped set SEND_TELEGRAM=1"
fi

echo "production_smoke=ok"
