---
name: system-status
description: Show a short privacy-safe operational status for the personal Hermes runtime.
---

# System status

For `/status`, `статус` or a question about why the agent is unavailable, call
`mcp_jarhert_native_system_status` once.

Report only factual, compact fields:

- gateway active/inactive;
- selected provider and model, without keys or quotas;
- Trello and Calendar health;
- cron job count, watchdog timer and stale heartbeat if present;
- backup timer state, encrypted backup readiness, count and age;
- aggregate native coding queue state, without prompts, results or errors;
- free disk, memory pressure and zombie-child count;
- deployed profile revision.

Do not expose task names, event names, contact data, tokens, paths, logs or raw
integration errors. If a component is unhealthy, name one next diagnostic step.
