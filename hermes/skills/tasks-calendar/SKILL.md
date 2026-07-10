---
name: tasks-calendar
description: Read Trello and Google Calendar directly, or execute all requested changes through one preview and one Telegram inline confirmation.
---

# Tasks and calendar

Use the `mcp_jarhert_native_*` tools. Never claim a task or event exists until
the native tool returns success. Do not use the terminal for these actions.

## Read without confirmation

Use `mcp_jarhert_native_task_list`, `mcp_jarhert_native_calendar_list`, and
`mcp_jarhert_native_integration_health`.

## Mutations use one plan

Convert the complete user request into one JSON array. Supported types are
`task.create`, `task.move`, `task.done`, `task.delete`, `calendar.create`,
`calendar.move`, and `calendar.delete`.

Call `mcp_jarhert_native_action_plan_create` once with the full `actions` array
and an idempotency key derived from the Telegram message ID.

Show one compact preview for every action. Call the native `clarify` tool once
with exactly two choices: `Выполнить` and `Отмена`. Telegram renders them as
inline buttons.

On `Выполнить`, call `mcp_jarhert_native_action_plan_execute` once with the
existing `plan_id` and `confirmed=true`, without another question.

On `Отмена`, call `mcp_jarhert_native_action_plan_cancel`. Reusing the same
Telegram update ID returns the existing plan. Summarize succeeded and failed
actions in one final message.
