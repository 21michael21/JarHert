#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

MAX_FAIL_RATE="${PROVIDER_GATE_MAX_FAIL_RATE:-0.20}"
MIN_QUALITY_SCORE="${PROVIDER_GATE_MIN_QUALITY_SCORE:-75}"
MAX_AVG_LATENCY_MS="${PROVIDER_GATE_MAX_AVG_LATENCY_MS:-12000}"
MAX_P95_LATENCY_MS="${PROVIDER_GATE_MAX_P95_LATENCY_MS:-20000}"
DIRECT_MIN_PASSING="${PROVIDER_GATE_DIRECT_MIN_PASSING:-1}"
LOCAL_MAX_FAIL_RATE="${PROVIDER_GATE_LOCAL_MAX_FAIL_RATE:-0.30}"
LOCAL_MIN_QUALITY_SCORE="${PROVIDER_GATE_LOCAL_MIN_QUALITY_SCORE:-70}"
LOCAL_MAX_AVG_LATENCY_MS="${PROVIDER_GATE_LOCAL_MAX_AVG_LATENCY_MS:-15000}"
LOCAL_MAX_P95_LATENCY_MS="${PROVIDER_GATE_LOCAL_MAX_P95_LATENCY_MS:-45000}"
RUN_LOCAL="${PROVIDER_GATE_RUN_LOCAL:-1}"
REQUIRE_LOCAL="${PROVIDER_GATE_REQUIRE_LOCAL:-0}"

if [[ "$#" -gt 0 ]]; then
  .venv/bin/python scripts/provider_benchmark.py \
    --gate \
  --max-fail-rate "$MAX_FAIL_RATE" \
  --min-quality-score "$MIN_QUALITY_SCORE" \
  --max-avg-latency-ms "$MAX_AVG_LATENCY_MS" \
  --max-p95-latency-ms "$MAX_P95_LATENCY_MS" \
  --min-passing-providers "$DIRECT_MIN_PASSING" \
  "$@"
  exit 0
fi

echo "provider_quality_gate=direct"
.venv/bin/python scripts/provider_benchmark.py \
  --gate \
  --allow-empty \
  --cost-mode free \
  --cost-mode cheap \
  --max-fail-rate "$MAX_FAIL_RATE" \
  --min-quality-score "$MIN_QUALITY_SCORE" \
  --max-avg-latency-ms "$MAX_AVG_LATENCY_MS" \
  --max-p95-latency-ms "$MAX_P95_LATENCY_MS" \
  --min-passing-providers "$DIRECT_MIN_PASSING"

echo "provider_quality_gate=local"
if [[ "$RUN_LOCAL" != "1" ]]; then
  echo "provider_quality_gate_local=skipped set PROVIDER_GATE_RUN_LOCAL=1"
  exit 0
fi

local_status=0
.venv/bin/python scripts/provider_benchmark.py \
  --gate \
  --allow-empty \
  --cost-mode local \
  --max-fail-rate "$LOCAL_MAX_FAIL_RATE" \
  --min-quality-score "$LOCAL_MIN_QUALITY_SCORE" \
  --max-avg-latency-ms "$LOCAL_MAX_AVG_LATENCY_MS" \
  --max-p95-latency-ms "$LOCAL_MAX_P95_LATENCY_MS" || local_status=$?

if [[ "$local_status" -ne 0 ]]; then
  echo "provider_quality_gate_local=advisory_fail"
  if [[ "$REQUIRE_LOCAL" == "1" ]]; then
    exit "$local_status"
  fi
fi
