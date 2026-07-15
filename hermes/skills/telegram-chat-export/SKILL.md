---
name: telegram-chat-export
description: Export text from a Telegram dialog accessible to the owner's MTProto account and return it as a TXT or JSONL document.
---

# Telegram chat export

This uses the owner's MTProto user session, not the bot token. It can export
only a peer present in that account's dialogs. It never downloads media.

## Confirm once

Accept only a numeric peer ID or `@username`. Default to TXT and 5000 messages
unless the user asks otherwise. Call
`mcp_jarhert_native_telegram_text_export_confirmed` exactly once with the peer,
format and limit. The tool owns the single Telegram confirmation and export.
Do not call `clarify` separately and do not use terminal for the export.

Do not read the resulting file into model context. Return a short count and the
attachment marker:

```text
Готово: <message_count> текстовых сообщений.
MEDIA:<path>
[[as_document]]
```

Put `MEDIA:<path>` and `[[as_document]]` on separate plain-text lines, never
inside a Markdown code fence. Do not replace the attachment with an empty
sentence like "вот выгрузка".

Mention when `truncated=true`. Never ask for a Telegram login code in chat. If
the session is unauthorized, tell the owner to run the local setup script in a
trusted terminal. Do not export a peer that is absent from the user's dialogs.

The file remains in the dedicated export directory only until `expires_at`
(48 hours by default) so Telegram can attach it. It is removed by the daily
cleanup timer.

## Read and analyze an owner-requested export

When the owner explicitly asks to read, summarize, search or analyze the export
they just requested, use `mcp_jarhert_native_telegram_text_export_excerpt`.
Use the returned text as data for the answer. Do not ask the owner to resend it
or pretend that metadata is an analysis. For a large export, say that the tool
returned a representative bounded sample and name that limitation plainly.

When the owner explicitly asks to send the export to Codex, research it deeply
or make a structured report, call
`mcp_jarhert_native_telegram_text_export_queue_analysis_confirmed` once. Its
single preview is the consent for sending the bounded sample to the isolated
Codex research runner. Do not request a second confirmation and do not make the
owner paste the file into chat. The raw sample is cleared from the queue when
the job finishes; return the runner's concise report when it arrives.
