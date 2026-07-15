#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${NATIVE_CHECK_PYTHON:-$ROOT/.venv/bin/python}"

native_tests=(
  tests/test_github_mcp_readonly.py
  tests/test_live_hermes_e2e_helpers.py
  tests/test_merge_hermes_tools.py
  tests/test_native_runtime_cutover.py
  tests/test_watchdog_timer_deploy.py
  tests/test_hermes_capability_modes.py
  tests/test_hermes_coding_queue.py
  tests/test_coding_runner_export_analysis.py
  tests/test_hermes_contact_workflow.py
  tests/test_hermes_dashboard.py
  tests/test_hermes_diff_monitors.py
  tests/test_hermes_event_graph.py
  tests/test_hermes_github_public.py
  tests/test_hermes_knowledge_archive.py
  tests/test_hermes_memory_consolidation.py
  tests/test_hermes_native_coding_jobs.py
  tests/test_hermes_native_mcp.py
  tests/test_hermes_operations.py
  tests/test_hermes_operator_canary.py
  tests/test_hermes_personal_os.py
  tests/test_hermes_personal_database.py
  tests/test_hermes_personal_productivity.py
  tests/test_hermes_personal_rhythms.py
  tests/test_hermes_prompt_budget.py
  tests/test_hermes_sandbox_worker.py
  tests/test_hermes_scheduled_delivery.py
  tests/test_hermes_shopping.py
  tests/test_hermes_skill_distillation.py
  tests/test_hermes_ssh_coding_queue.py
  tests/test_hermes_subscriptions.py
  tests/test_hermes_system_status.py
  tests/test_hermes_task_calendar_adapter.py
  tests/test_hermes_task_calendar_plans.py
  tests/test_hermes_telegram_text_export.py
  tests/test_hermes_trip_store.py
  tests/test_hermes_trips.py
  tests/test_hermes_voice_inbox.py
  tests/test_hermes_watchdog.py
)

cd "$ROOT"
"$PYTHON" -m pytest -q "${native_tests[@]}"
"$PYTHON" -m compileall hermes/native_tools hermes/scripts deploy/vps
