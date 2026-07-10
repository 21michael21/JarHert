---
name: skill-distillation
description: Distill three confirmed successful repeats into one reviewable procedural skill without storing raw conversations.
---

# Skill distillation

Use this only after a tool-backed workflow succeeded and the user explicitly
confirmed that its result was useful. Approval before execution is not outcome
confirmation.

## Record an observation

Describe the reusable procedure with two to twelve allowlisted tool categories.
Use placeholders rather than names, IDs, URLs, message text, or local paths.

```bash
python "$HERMES_HOME/native_tools/cli.py" skill observe \
  --workflow-key "morning-plan" \
  --title "Утренний план" \
  --steps-json '[{"tool":"personal_operating_center","summary":"Собрать календарь и задачи"},{"tool":"personal_memory","summary":"Добавить обещания и блокеры"},{"tool":"telegram_delivery","summary":"Отправить короткий итог"}]' \
  --idempotency-key "telegram-update-<update_id>" \
  --success --confirmed
```

The update ID makes replay harmless. Failed, partial, or unconfirmed outcomes
must be recorded without one or both flags and never advance the threshold.

## Stage the skill

When `status` becomes `ready_for_review` after three unique confirmations:

1. Read the candidate with `skill show <workflow-key>`.
2. Call `skill_manage(action="create", name=<skill_name>, content=<skill_markdown>)` once.
3. The write-approval gate stages a diff. Tell the user to review it with
   `/skills pending` and `/skills diff <id>`.
4. Only after `skill_manage` reports a staged write, run
   `skill mark-staged <workflow-key>`.

Never bypass the approval gate. Never make a skill from one impressive result,
raw chat history, credentials, private content, or an unverified model answer.
