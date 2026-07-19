---
name: forward-triage
description: Sort a forwarded Telegram message into a task, note, knowledge link or contact update with one plan confirmation.
---

# Forward Triage

Use when the owner forwards a message (the gateway prefixes it with
`[Переслано из: ...]`) without an explicit command. Do not triage plain
conversation, memes or questions — answer those normally. One forwarded
message produces at most one triage plan; never fire several tools in a row
without the single preview.

## Classify the content first

Decide what the forward actually is before choosing any action:

- **Поручение или дедлайн** (просят сделать, назначена дата) → `task.create`
  через plan. Заголовок — короткая суть, не весь текст сообщения.
- **Полезный факт, идея, договорённость** → `note.save` с предметом и сутью
  своими словами, источник из префикса — в content или project.
- **Ссылка http(s)** → не раскладывай в заметку: предложи сохранить страницу
  через `mcp_jarhert_native_knowledge_archive_url_confirmed` с project по
  смыслу. Несколько ссылок — `knowledge_archive_urls_confirmed`.
- **Информация о человеке** (контакт, роль, договорённость с ним) →
  `crm_interaction_log` через plan; если человека нет в контактах, сначала
  `contact_add` в том же plan.
- **Просьба спарсить/выгрузить чат** → сразу telegram-chat-export flow, без
  triage plan.

## Rules

- Всегда один preview — одно подтверждение через
  `mcp_jarhert_native_action_plan_confirm_execute`. Не создавай ничего без
  подтверждения и не говори «сохранил» до успеха инструмента.
- Если тип неочевиден — задай один короткий вопрос («задача или заметка?»)
  вместо угадывания. Тишина форварда тоже валидный ответ: предложи, не навязывай.
- Не копируй чужой текст дословно длиннее одной строки; перескажи суть.
- Источник из префикса `[Переслано из: ...]` указывай в заметке/CRM, чтобы
  потом было понятно, откуда факт.
