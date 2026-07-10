---
name: telegram-chat-export
description: Export text from a Telegram dialog accessible to the owner's MTProto account and return it as a TXT or JSONL document.
---

# Telegram chat export

This uses the owner's MTProto user session, not the bot token. It can export
only a peer present in that account's dialogs. It never downloads media.

## Confirm once

Accept only a numeric peer ID or `@username`. Default to TXT and 5000 messages
unless the user asks otherwise. Call native `clarify` once with two choices:
`Экспортировать` and `Отмена`. Telegram renders these as inline buttons.

After `Экспортировать`, call `mcp_jarhert_native_telegram_text_export` exactly
once with `confirmed=true`, `output_format=txt`, the peer and the requested
limit. Do not use terminal for the export.

Do not read the resulting file into model context. Return a short count and the
attachment marker:

```text
Готово: <message_count> текстовых сообщений.
MEDIA:<path>
[[as_document]]
```

Mention when `truncated=true`. Never ask for a Telegram login code in chat. If
the session is unauthorized, tell the owner to run the local setup script in a
trusted terminal. Do not export a peer that is absent from the user's dialogs.
