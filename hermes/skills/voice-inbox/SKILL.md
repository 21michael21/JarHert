---
name: voice-inbox
description: Split one voice transcript into notes, commitments, tasks, and meetings with one plan confirmation.
---

# Voice Inbox

Use when Telegram provides a voice transcript containing several thoughts or
actions. Do not transcribe audio inside this skill; the gateway already provides
text.

Before interpreting a transcript, call `mcp_jarhert_native_voice_inbox_prepare`.
Use its `text` as the working transcript. It only applies the owner's explicit
vocabulary corrections and normalizes whitespace; it never invents wording,
dates, contacts, or actions. If the user corrects a recurring proper noun or
project name, save it through `mcp_jarhert_native_voice_vocabulary_add` and
continue with the same single inbox plan.

`mode=command` means one short direct instruction: resolve it without
turning it into a verbose voice dump. `mode=inbox` means several clear items:
collect them into one preview. `mode=dictation` has no clear side effect;
answer it normally or offer to save it as a note when that is useful.

Before selecting tools, build an internal JSON plan. Never show this JSON to the
user and never execute it directly:

```json
{"actions": [{"type": "calendar.create", "payload": {}}], "followups": ["short clarification"]}
```

`actions` contains every clear side effect in transcript order. `followups`
contains only missing details or questions that cannot be answered safely. One
unclear thought must not discard or delay the other clear actions.

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
8. Если в transcript смешаны действия и вопрос, не теряй вопрос. Ясные действия
   собирай в один plan, а на вопрос отвечай отдельно и только по проверяемым
   данным. Для фильма, книги или другого объекта с неоднозначным названием спроси
   год или ссылку вместо догадки.
9. Встреча с указанным только началом получает длительность 60 минут, которую
   нужно показать в preview. «Через неделю в этот же день» связывай с ближайшей
   ранее явно названной датой и временем, только когда связь однозначна.
10. Если не назван получатель для отправки расписания или сообщения, не исполняй
    отправку. Остальные ясные пункты оставь в plan и задай один короткий вопрос
    только об адресате.
11. Если actions не пуст, передай их одним `action_plan_confirm_execute` и покажи
    один human preview. После preview добавь followups двумя-тремя короткими
    строками. Не превращай один voice dump в цепочку уточнений.

The tool result is the source of truth. Report succeeded and failed items once;
never claim a side effect from the proposed plan alone.
