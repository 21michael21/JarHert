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
   `task.create`, and `calendar.create`.
3. Use a commitment when the user promised something to a person. Include
   `contact`, `project`, and timezone-aware `due_at` only when stated.
4. Use one `mcp_jarhert_native_action_plan_confirm_execute` call for the entire
   transcript and one idempotency key for that Telegram voice message.
5. If a missing time changes a meeting or deadline, ask one question before
   creating the plan. Otherwise continue with a reasonable non-destructive item.

The tool result is the source of truth. Report succeeded and failed items once;
never claim a side effect from the proposed plan alone.
