#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${RELEASE_GATE_PYTHON:-$ROOT/.venv/bin/python}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="${RELEASE_GATE_REPORT_DIR:-$ROOT/reports/release_95/$RUN_ID}"
RESULTS_JSONL="$REPORT_DIR/gates.jsonl"
SCORECARD="$REPORT_DIR/scorecard.json"

mkdir -p "$REPORT_DIR"
: > "$RESULTS_JSONL"
cd "$ROOT"

now_ms() {
  "$PYTHON" -c 'import time; print(time.time_ns() // 1_000_000)'
}

record_result() {
  local name="$1" status="$2" duration_ms="$3" log="$4" detail="${5:-}"
  GATE_NAME="$name" GATE_STATUS="$status" GATE_DURATION_MS="$duration_ms" \
    GATE_LOG="$log" GATE_DETAIL="$detail" "$PYTHON" - "$RESULTS_JSONL" <<'PY'
import json
import os
import sys
from pathlib import Path

payload = {
    "name": os.environ["GATE_NAME"],
    "status": os.environ["GATE_STATUS"],
    "duration_ms": int(os.environ["GATE_DURATION_MS"]),
    "log": os.environ["GATE_LOG"],
    "detail": os.environ["GATE_DETAIL"],
}
with Path(sys.argv[1]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
PY
}

run_gate() {
  local name="$1"
  shift
  local log="$REPORT_DIR/$name.log" started finished status exit_code
  started="$(now_ms)"
  echo "[$name] running"
  if "$@" >"$log" 2>&1; then
    status="passed"
    exit_code=0
  else
    exit_code=$?
    status="failed"
  fi
  finished="$(now_ms)"
  record_result "$name" "$status" "$((finished - started))" "$log" "exit_code=$exit_code"
  echo "[$name] $status (${exit_code}) log=$log"
  if [[ "$status" == "failed" ]]; then
    tail -n 30 "$log" || true
  fi
}

clean_clone_gate() {
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "working tree is not clean"
    return 1
  fi
  local temp_dir clone database_url status=0
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/jarhert-clean-clone.XXXXXX")"
  clone="$temp_dir/repo"
  database_url="sqlite:///$temp_dir/clean.sqlite3"
  git clone --quiet --local "$ROOT" "$clone" || status=$?
  if [[ "$status" -eq 0 ]]; then
    (
      cd "$clone"
      DATABASE_URL="$database_url" "$PYTHON" scripts/run_migrations.py
      ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake \
        DATABASE_URL="$database_url" "$PYTHON" -m pytest -q
    ) || status=$?
  fi
  rm -rf "$temp_dir"
  return "$status"
}

migrations_gate() {
  local temp_dir database_url status=0
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/jarhert-migrations.XXXXXX")"
  database_url="sqlite:///$temp_dir/migrations.sqlite3"
  DATABASE_URL="$database_url" "$PYTHON" scripts/run_migrations.py || status=$?
  if [[ "$status" -eq 0 ]]; then
    ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake \
      "$PYTHON" -m pytest -q tests/test_migration_lifecycle.py || status=$?
  fi
  rm -rf "$temp_dir"
  return "$status"
}

tests_gate() {
  ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake "$PYTHON" -m pytest -q
}

golden_gate() {
  ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake "$PYTHON" scripts/eval_golden.py
}

provider_gate() {
  local args=(
    --gate
    --max-fail-rate "${PROVIDER_GATE_MAX_FAIL_RATE:-0.20}"
    --min-quality-score "${PROVIDER_GATE_MIN_QUALITY_SCORE:-75}"
    --max-avg-latency-ms "${PROVIDER_GATE_MAX_AVG_LATENCY_MS:-12000}"
    --max-p95-latency-ms "${PROVIDER_GATE_MAX_P95_LATENCY_MS:-20000}"
  )
  if [[ -n "${PROVIDER_GATE_MIN_PASSING_PROVIDERS:-}" ]]; then
    args+=(--min-passing-providers "$PROVIDER_GATE_MIN_PASSING_PROVIDERS")
  fi
  "$PYTHON" scripts/provider_benchmark.py "${args[@]}"
}

security_gate() {
  "$PYTHON" scripts/security_scan.py &&
    "$PYTHON" -m pip check &&
    ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake "$PYTHON" -m pytest -q \
      tests/test_tool_registry.py tests/test_quality_gates.py tests/test_config.py tests/test_backend_api.py
}

concurrency_gate() {
  ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake "$PYTHON" -m pytest -q \
    tests/test_pipeline_concurrency.py tests/test_blocking_executor.py \
    tests/test_item_leases.py tests/test_update_idempotency.py
}

load_gate() {
  "$PYTHON" scripts/local_load_test.py \
    --requests "${RELEASE_GATE_LOAD_REQUESTS:-200}" \
    --concurrency "${RELEASE_GATE_LOAD_CONCURRENCY:-16}" \
    --max-p95-ms "${RELEASE_GATE_LOAD_MAX_P95_MS:-1000}" \
    --report "$REPORT_DIR/load.json"
}

kill_worker_gate() {
  ADMIN_TG_USER_IDS= ALLOWED_TG_USER_IDS= HERMES_MODE=fake "$PYTHON" -m pytest -q \
    tests/test_item_leases.py tests/test_automation_runtime.py \
    -k "killed or expired_lease or stale"
}

backup_restore_gate() {
  "$PYTHON" scripts/backup_restore_check.py --report "$REPORT_DIR/backup-restore.json"
}

live_telegram_gate() {
  local tg_user_id="${RELEASE_GATE_TG_USER_ID:-${TG_USER_ID:-}}"
  local voice_file="${RELEASE_GATE_VOICE_FILE:-}"
  if [[ -z "$tg_user_id" ]]; then
    echo "Set RELEASE_GATE_TG_USER_ID"
    return 1
  fi
  if [[ -z "$voice_file" || ! -f "$voice_file" ]]; then
    echo "Set RELEASE_GATE_VOICE_FILE to an existing .oga/.m4a fixture"
    return 1
  fi
  local args=(
    --mode live
    --require-live
    --tg-user-id "$tg_user_id"
    --voice-file "$voice_file"
    --report-path "$REPORT_DIR/live-system-e2e.json"
  )
  if [[ -n "${RELEASE_GATE_BOT_USERNAME:-}" ]]; then
    args+=(--bot-username "$RELEASE_GATE_BOT_USERNAME")
  fi
  "$PYTHON" scripts/live_system_e2e.py "${args[@]}"
}

run_gate "clean_clone" clean_clone_gate
run_gate "migrations" migrations_gate
run_gate "tests" tests_gate
run_gate "golden_eval" golden_gate
run_gate "provider_benchmark" provider_gate
run_gate "security_scan" security_gate
run_gate "concurrency" concurrency_gate
run_gate "load" load_gate
run_gate "kill_worker_recovery" kill_worker_gate
run_gate "backup_restore" backup_restore_gate
if [[ "${RELEASE_GATE_SKIP_LIVE:-0}" == "1" ]]; then
  record_result "live_telegram_e2e" "skipped" 0 "" "RELEASE_GATE_SKIP_LIVE=1"
  echo "[live_telegram_e2e] skipped (release remains ineligible for 9.5)"
else
  run_gate "live_telegram_e2e" live_telegram_gate
fi

commit="$(git rev-parse HEAD 2>/dev/null || true)"
scorecard_status=0
"$PYTHON" scripts/release_scorecard.py \
  --results-jsonl "$RESULTS_JSONL" \
  --output "$SCORECARD" \
  --commit "$commit" || scorecard_status=$?
echo "release_95_gate report=$SCORECARD"
exit "$scorecard_status"
