---
name: contact-messaging
description: Save Telegram contacts and aliases, prepare one preview for one or many outgoing messages, confirm the whole plan once, and schedule delivery. Use for requests such as "напомни написать Илье", "подготовь сообщение", "отправь завтра", contact management, and deferred Telegram messages.
---

# Contact messaging

Use only the `jarhert_native` MCP tools below. Their structured result is the
source of truth. Never use terminal commands for contacts or message plans.

## Contacts

Resolve only an exact saved name or alias, ignoring letter case. Never choose a
similar contact by fuzzy match.

Call `mcp_jarhert_native_contact_list` to inspect saved contacts and
`mcp_jarhert_native_contact_add` to save an exact name, Telegram chat ID, and
aliases.

If a contact is missing, ask for their Telegram chat ID once. Do not search
private chats, infer an ID, or expose the contact book.

## One preview and one confirmation

Build one array for the complete request. Every item needs `contact`, `text`,
and an ISO timestamp with timezone in `send_at`. Call
`mcp_jarhert_native_message_plan_confirm_schedule` exactly once. The tool owns
the preview, the single confirmation, scheduling, and idempotent replay.

To cancel a complete draft or scheduled plan, call
`mcp_jarhert_native_message_plan_cancel_confirmed`. It owns the confirmation.

Reuse the same idempotency key when retrying one tool call. The store returns
the original plan and cannot schedule a duplicate.

## Delivery

Delivery is handled by one script-only Hermes cron job. It uses no model and
prints nothing on a healthy tick.

```bash
hermes cron create "* * * * *" \
  --name "Personal OS message dispatcher" \
  --script dispatch_due_messages.py --no-agent --deliver local
```

Do not create a cron job per message. Do not send through Telegram directly as
part of planning. The dispatcher records success, Telegram result ID, attempts,
and the final error.
