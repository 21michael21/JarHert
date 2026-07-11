---
name: personal-memory
description: Save, find, connect, and correct personal notes without losing context.
---

# Personal Memory

## When to use

Use for ideas, promises, project decisions, people, meeting outcomes, and
requests such as `find notes about OAuth` or `what did I promise Ilya`.

## Procedure

1. Use `mcp_jarhert_native_memory_block_upsert` only when the user explicitly
   asks to remember a stable profile, person, project, commitment, or preference
   fact. Do not save ordinary chat automatically.
2. Use `mcp_jarhert_native_memory_block_list` with a type/project filter before
   answering personal-memory questions. Return only the matching block group.
3. Search by words first. Return short excerpts and dates, never a giant dump.
4. For promises, call `mcp_jarhert_native_commitment_list` with the named contact
   or project. Mark one done only through
   `mcp_jarhert_native_commitment_complete_confirmed`.
5. When editing or deleting a pronoun reference such as `change it`, resolve
   it against the latest matching note. If there are two plausible matches,
   ask one short question.
6. Keep original text and an edit history. Never rewrite a user's note silently.

## Guardrails

- Do not save passwords, API keys, recovery codes, or private keys as memory.
- Do not claim a note was saved before the storage tool confirms it.
- Do not use raw conversation history as a training dataset without explicit
  user consent.
- Resolve project wording through `mcp_jarhert_native_project_context_resolve`.
  Create or update project context only after an explicit request, using
  `mcp_jarhert_native_project_context_upsert`.

## Verification

- A search result identifies the source note and its project/contact link.
- An edit preserves a recoverable previous revision.
- No unrelated person's note appears in a query.
