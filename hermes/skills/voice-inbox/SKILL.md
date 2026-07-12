---
name: voice-inbox
description: Split one voice transcript into notes, commitments, tasks, and meetings with one plan confirmation.
---

# Voice Inbox

Use when Telegram provides a voice transcript containing several thoughts or
actions. Do not transcribe audio inside this skill; the gateway already provides
text.

1. Preserve the user's wording. Do not invent deadlines, contacts, or projects.
2. Map useful items to only these actions: `note.save`, `commitment.create`,
   `reminder.create`, `task.create`, and `calendar.create`.
3. Use a commitment when the user promised something to a person. Include
   `contact`, `project`, and timezone-aware `due_at` only when stated.
4. Use one `mcp_jarhert_native_action_plan_confirm_execute` call for the entire
   transcript and one idempotency key for that Telegram voice message.
5. If the transcript is noisy, keep every clear non-destructive item and ask
   one short question only for a missing detail that changes the action. Do not
   guess deadlines, but do not discard the whole inbox because one fragment is
   unclear.
6. Не проси пользователя переписывать голосовое, перечислять пункты по шаблону
   или присылать второе сообщение ради форматирования. Покажи один понятный plan
   из того, что удалось разобрать.
7. Для длинного dump или нескольких смысловых пунктов добавь в тот же plan
   `note.save` с subject `Голосовой черновик` и исходной расшифровкой в content.
   Не пересказывай её и не создавай такой черновик для одной короткой команды.

The tool result is the source of truth. Report succeeded and failed items once;
never claim a side effect from the proposed plan alone.
