---
name: event-monitors
description: Create and operate diff-first proactive monitors that stay silent until a source really changes. Use for GitHub release watching, conditions such as "напиши только если", monitor listing, disabling, and cron setup.
---

# Event monitors

Use the deterministic CLI at `$HERMES_HOME/native_tools/cli.py`. It hashes a
small normalized source payload before any model is involved.

## Create and manage

Only `github_releases` is allowlisted in this version.

```bash
python "$HERMES_HOME/native_tools/cli.py" monitor add \
  --name "codex-releases" \
  --source-type github_releases \
  --source-config-json '{"owner":"openai","repo":"codex"}' \
  --condition "Напиши только если в релизе есть важные возможности"
python "$HERMES_HOME/native_tools/cli.py" monitor list
python "$HERMES_HOME/native_tools/cli.py" monitor remove <monitor_id>
```

`remove` disables the monitor and keeps its state for audit.

## Check workflow

Run all enabled monitors with:

```bash
python "$HERMES_HOME/native_tools/cli.py" monitor check
```

An empty result means baseline or no change. Say nothing and do not call a
provider. For each changed item, evaluate only its `diff`, `current`, and
`condition`:

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
