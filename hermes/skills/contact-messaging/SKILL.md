---
name: contact-messaging
description: Save Telegram contacts and aliases, prepare one preview for one or many outgoing messages, confirm the whole plan once, and schedule delivery. Use for requests such as "напомни написать Илье", "подготовь сообщение", "отправь завтра", contact management, and deferred Telegram messages.
---

# Contact messaging

Use the deterministic CLI at `$HERMES_HOME/native_tools/cli.py`. Treat its JSON
as the source of truth. Never claim success from the command text alone.

## Contacts

Resolve only an exact saved name or alias, ignoring letter case. Never choose a
similar contact by fuzzy match.

```bash
python "$HERMES_HOME/native_tools/cli.py" contact list
python "$HERMES_HOME/native_tools/cli.py" contact add \
  --name "Илья" --telegram-chat-id 123456 --alias "Ильюха" --alias "Илье"
```

If a contact is missing, ask for their Telegram chat ID once. Do not search
private chats, infer an ID, or expose the contact book.

## One preview and one confirmation

Build one JSON array for the complete request. Every item needs `contact`,
`text`, and an ISO timestamp with timezone in `send_at`.

```bash
python "$HERMES_HOME/native_tools/cli.py" message plan \
  --idempotency-key "telegram-update-<update_id>" \
  --items-json '[{"contact":"Илья","text":"Созвонимся завтра?","send_at":"2030-01-02T12:00:00+03:00"}]'
```

Show one compact preview containing all recipients, texts, and send times. Ask
one question: `Запланировать?` Do not run `approve` before an explicit yes.

After confirmation, approve the whole plan once:

```bash
python "$HERMES_HOME/native_tools/cli.py" message approve <plan_id>
```

Replaying the same Telegram update ID returns the same plan and cannot schedule
a duplicate.

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

