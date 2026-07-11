---
name: subscriptions
description: Track recurring payments, upcoming charges, monthly totals, and cancellation reminders.
---

# Subscriptions

Use `mcp_jarhert_native_subscription_*` for create, list, update, and cancel.
Preserve currencies separately and never invent exchange rates. Each active
subscription owns one charge reminder; update moves it and cancellation removes
it. Reuse the Telegram update ID as the create idempotency key.

SQLite is the source of truth. Optional Sheets sync is configured outside the
model with `SUBSCRIPTION_SYNC_COMMAND`. The command receives a JSON array on
stdin, runs without a shell, and is best effort: a sync failure must not roll
back the subscription.
