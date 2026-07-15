---
name: telegram-chat-export
description: Export and analyze text or download small documents from a Telegram dialog accessible to the owner's MTProto account.
---

# Telegram chat export

This uses the owner's MTProto session, not the bot token. It works only for a
numeric peer ID or `@username` present in that account's dialogs. Never ask for
a Telegram login code in chat.

## Text export

Choose the message count from the request: `последние N` means exactly N,
`весь чат` means 50000, and no count means the useful default of 5000. Do not
ask a separate question if this default is sufficient. Call
`mcp_jarhert_native_telegram_text_export_confirmed` once with peer, format and
limit; its preview is the only confirmation.

After success, copy the returned `attachment.directive` exactly into the final
reply, on separate plain-text lines. It sends the TXT/JSONL as a Telegram
document. Do not replace it with an empty “вот выгрузка” sentence or invent a
path. Mention `message_count`, `truncated` if true, and the expiry time.

The directive has `[[as_document]]` on the first line and `MEDIA:<path>` on
the second line. Copy it unchanged.

Exports remain in the dedicated directory for 48 hours by default, then the
cleanup timer removes them.

## Download documents

When the owner asks for files from a dialog, call
`mcp_jarhert_native_telegram_file_download_confirmed` once. Use explicit
message IDs if they were given; otherwise use a sensible `file_limit` and
`scan_limit`. It downloads documents only, at most 20 files per request and
20 MB per file, into the same short-lived directory. Files above the cap are
reported as skipped. Copy every returned `attachment.directive` exactly into
the final reply so the owner receives the documents.

## Read and analyze an owner-requested export

Не читай экспорт автоматически: сначала дождись явной просьбы прочитать,
разобрать или ответить по нему. Но когда она есть, не отказывайся читать и не
подменяй анализ метаданными. Вызови
`mcp_jarhert_native_telegram_text_export_excerpt`, используй returned text как
данные и дай прямой ответ или полезный фидбэк. Для большого экспорта честно
скажи, что это ограниченная репрезентативная выборка.

Когда владелец просит глубокий отчёт, Codex-анализ или структурный разбор,
вызови `mcp_jarhert_native_telegram_text_export_queue_analysis_confirmed` один
раз. Его preview — единственное согласие на передачу ограниченной выборки в
изолированный research runner. Не проси второй раз вставить файл в чат. После
завершения верни короткий человеческий отчёт.
