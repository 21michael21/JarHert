---
name: sandboxed-coding
description: Research or change code in an isolated workspace with reviewable results.
version: 0.1.0
platforms: [linux, macos]
---

# Sandboxed Coding

## When to use

Use for repository research, implementation, tests, code review, or generated
artifacts that require files or commands.

## Procedure

1. Work only in the task workspace or an isolated git worktree.
2. Inspect the relevant files before changing them.
3. Write a failing test when behavior changes, then implement the smallest fix.
4. Run the relevant checks and report exact results.
5. Produce a diff and a short explanation before any merge, push, deploy, or
   host-side command.

## Guardrails

- Never read `.env`, SSH keys, Docker socket, or files outside the workspace.
- Never deploy, merge, push, delete data, or change production configuration
  without an explicit approval in the current conversation.
- Do not use a host shell for code tasks; use the Hermes sandbox backend.

## Verification

- The task has a clean diff and fresh test output.
- The sandbox contains no credentials passed through by default.
- The final action needing external side effects has an approval preview.

