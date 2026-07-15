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

Call `mcp_jarhert_native_action_plan_confirm_execute` once with the full
`actions` array and an idempotency key derived from the Telegram message ID.

The native tool owns the compact preview, the single Telegram confirmation,
idempotent execution, one batch connection to Task Command Center, and
cancellation. Do not call `clarify`, approve, execute or cancel separately.
Summarize succeeded and failed actions in one final message.

For a compact human status of a long plan, call
`mcp_jarhert_native_action_plan_trace`. Use the full
`mcp_jarhert_native_action_plan_status` only when the user asks for details or
when diagnosing a failure.
