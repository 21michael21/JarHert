---
name: tasks-calendar
description: Read Trello and Google Calendar directly, or execute all requested changes through one preview and one Telegram inline confirmation.
---

# Tasks and calendar

Use `$HERMES_HOME/native_tools/cli.py`. Never claim a task or event exists until
the command returns success.

## Read without confirmation

```bash
python "$HERMES_HOME/native_tools/cli.py" task list --list-name Today
python "$HERMES_HOME/native_tools/cli.py" calendar list --when today
python "$HERMES_HOME/native_tools/cli.py" integration-health
```

## Mutations use one plan

Convert the complete user request into one JSON array. Supported types are
`task.create`, `task.move`, `task.done`, `task.delete`, `calendar.create`,
`calendar.move`, and `calendar.delete`.

```bash
python "$HERMES_HOME/native_tools/cli.py" plan create \
  --idempotency-key "telegram-update-<update_id>" \
  --actions-json '[{"type":"task.create","payload":{"title":"Проверить релиз","list_name":"Today"}},{"type":"calendar.create","payload":{"title":"Проверить релиз","start":"2030-01-02 12:00","end":"2030-01-02 12:30"}}]'
```

Show one compact preview for every action. Call the native `clarify` tool once
with exactly two choices: `Выполнить` and `Отмена`. Telegram renders them as
inline buttons.

On `Выполнить`, run both commands without another question:

```bash
python "$HERMES_HOME/native_tools/cli.py" plan approve <plan_id>
python "$HERMES_HOME/native_tools/cli.py" plan execute <plan_id>
```

On `Отмена`, run `plan cancel <plan_id>`. Reusing the same Telegram update ID
returns the existing plan. Summarize succeeded and failed actions in one final
message. Do not use direct mutation commands with `--confirmed`; those exist
only for operator diagnostics and canary cleanup.
