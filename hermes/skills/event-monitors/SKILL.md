---
name: event-monitors
description: Create and operate diff-first proactive monitors that stay silent until a source really changes. Use for GitHub release watching, conditions such as "напиши только если", monitor listing, disabling, and cron setup.
---

# Event monitors

Use only the `jarhert_native` MCP tools for user-facing monitor management. The
background runner hashes a small normalized source payload before any model is
involved.

## Create and manage

Allowlisted source types are `github_releases`, `rss`, `json_api`, and
`allowed_url`. URL sources require HTTPS and an explicit hostname allowlist;
private literal IPs, credentials in URLs, oversized responses, scripts, and
styles are rejected.

Call `mcp_jarhert_native_monitor_add_github_releases` with the monitor name,
owner, repository, and condition. Use `mcp_jarhert_native_monitor_list` to list
monitors and `mcp_jarhert_native_monitor_disable` to disable one.

`remove` disables the monitor and keeps its state for audit.

For URL sources call `mcp_jarhert_native_monitor_add_source`. Add quiet hours as
`HH:MM-HH:MM`; changes during that interval and changes over the daily model
budget go to one digest instead of waking the model immediately.

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

Create one digest cron. Read item IDs from its output, write one compact
summary, then call `mcp_jarhert_native_monitor_digest_mark_delivered` once:

```bash
hermes cron create "0 8 * * *" \
  --name "Monitor digest" \
  --script check_monitor_digest.py \
  --skill event-monitors \
  --deliver origin
```
