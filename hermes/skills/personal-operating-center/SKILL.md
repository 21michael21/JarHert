---
name: personal-operating-center
description: Run a concise personal daily plan, review, and inbox triage.
---

# Personal Operating Center

## When to use

Use when the user asks what is planned today or tomorrow, wants to unload a
messy inbox, pick priorities, review the day, or prepare the next one.

## Procedure

1. Resolve the project with `mcp_jarhert_native_project_context_resolve` when a
   project is named.
2. For `что у меня сегодня`, call `mcp_jarhert_native_personal_today` once.
   It returns factual sources and the deterministic top three.
3. For `разгрузи голову`, turn each distinct item into a note, reminder, task,
   or question. Show one compact plan before creating medium-risk items.
4. For `выбери три задачи`, start with `top_three`; change the selection only
   when the user gives an explicit priority or constraint.
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
