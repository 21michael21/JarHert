---
name: personal-operating-center
description: Run a concise personal daily plan, review, and inbox triage.
---

# Personal Operating Center

## When to use

Use when the user asks what is planned today or tomorrow, wants to unload a
messy inbox, pick priorities, review the day, or prepare the next one.

## Procedure

1. Resolve the project with `mcp_jarhert_native_project_context_resolve`, then
   read matching commitments via `mcp_jarhert_native_memory_block_list`.
2. For `what is today`, combine calendar blocks, due tasks, reminders, and
   explicitly saved commitments. Do not invent unavailable data.
3. For `unload my head`, turn each distinct item into a note, reminder, task,
   or question. Show one compact plan before creating medium-risk items.
4. Pick at most three priorities. State the trade-off for items left out.
5. For an evening review, report completed work, unfinished commitments,
   blockers, and the smallest useful first step for tomorrow.
6. Keep the final reply short. Ask one question only if a missing fact changes
   the action.

## Guardrails

- A reminder, Calendar event, task, or outgoing message is real only after its
  corresponding tool reports success.
- Do not expose note contents, contacts, credentials, or project files to a
  different person.
- Do not run shell commands outside the dedicated Personal OS tool or sandbox
  workspace.

## Verification

- A daily plan names only items actually returned by the tools.
- A review contains no invented completion.
- A multi-action request has one clear approval point, not one confirmation per
  line item.
