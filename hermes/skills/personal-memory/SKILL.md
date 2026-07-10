---
name: personal-memory
description: Save, find, connect, and correct personal notes without losing context.
version: 0.1.0
---

# Personal Memory

## When to use

Use for ideas, promises, project decisions, people, meeting outcomes, and
requests such as `find notes about OAuth` or `what did I promise Ilya`.

## Procedure

1. Store a note with a concrete type: idea, promise, decision, reference, or
   follow-up.
2. Link the note to a project, contact, task, or calendar event when the user
   names one. Ask only when the link changes the meaning.
3. Search by words first. Return short excerpts and dates, never a giant dump.
4. When editing or deleting a pronoun reference such as `change it`, resolve
   it against the latest matching note. If there are two plausible matches,
   ask one short question.
5. Keep original text and an edit history. Never rewrite a user's note silently.

## Guardrails

- Do not save passwords, API keys, recovery codes, or private keys as memory.
- Do not claim a note was saved before the storage tool confirms it.
- Do not use raw conversation history as a training dataset without explicit
  user consent.

## Verification

- A search result identifies the source note and its project/contact link.
- An edit preserves a recoverable previous revision.
- No unrelated person's note appears in a query.

