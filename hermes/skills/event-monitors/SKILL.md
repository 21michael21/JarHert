---
name: event-monitors
description: Create and operate diff-first proactive monitors that stay silent until a source really changes. Use for GitHub release watching, conditions such as "напиши только если", monitor listing, disabling, and cron setup.
---

# Event monitors

Use only the `jarhert_native` MCP tools for user-facing monitor management. The
background runner hashes a small normalized source payload before any model is
involved.

## Create and manage

Only `github_releases` is allowlisted in this version.

Call `mcp_jarhert_native_monitor_add_github_releases` with the monitor name,
owner, repository, and condition. Use `mcp_jarhert_native_monitor_list` to list
monitors and `mcp_jarhert_native_monitor_disable` to disable one.

`remove` disables the monitor and keeps its state for audit.

## Check workflow

The internal cron script checks all enabled monitors. An empty result means
baseline or no change. Say nothing and do not call a provider. For each changed
item, evaluate only its `diff`, `current`, and `condition`:

1. If the condition is false, return no Telegram message.
2. If the condition is true, write one short factual message with the source URL.
3. Do not invent details absent from the payload.
4. Do not paste the full changelog when a short summary is enough.

## Cron

Create one agentful cron job. The script itself prints nothing for baseline and
no-change checks, so Hermes wakes the model only when the output contains a
real diff.

```bash
hermes cron create "every 30m" \
  --name "Personal diff monitors" \
  --script check_monitors.py \
  --skill event-monitors \
  --deliver origin
```

Do not create one cron per check tick. Never replace the deterministic hash and
diff with a scheduled prompt that always calls the model.
