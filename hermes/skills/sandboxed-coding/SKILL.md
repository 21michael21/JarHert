---
name: sandboxed-coding
description: Run coding or source-bounded research through the native Hermes Docker backend with reviewable results and no forwarded secrets.
---

# Sandboxed Coding

## When to use

Use for repository research, implementation, tests, code review, or bounded
web research that needs files or commands. Launch it through the deterministic
wrapper rather than changing the main chat's terminal backend.

## Launch

Coding accepts only a public GitHub HTTPS repository:

```bash
python "$HERMES_HOME/native_tools/cli.py" sandbox run \
  --mode coding \
  --repository-url "https://github.com/owner/repository.git" \
  --prompt "Добавь тест, исправь причину и покажи diff"
```

Research accepts only HTTPS hosts from `HERMES_RESEARCH_ALLOWED_HOSTS`:

```bash
python "$HERMES_HOME/native_tools/cli.py" sandbox run \
  --mode research \
  --source-url "https://github.com/owner/repository/releases" \
  --prompt "Сравни последние релизы и назови фактические изменения"
```

## Procedure

1. Work only in the task workspace or an isolated git worktree.
2. Inspect the relevant files before changing them.
3. Write a failing test when behavior changes, then implement the smallest fix.
4. Run the relevant checks and report exact results.
5. Produce a diff and a short explanation before any merge, push, deploy, or
   host-side command.
6. For independent read-only branches, delegate at most three subagents. The
   parent owns the final diff and verification; subagents never deploy.

## Guardrails

- Never read `.env`, SSH keys, Docker socket, or files outside the workspace.
- Never deploy, merge, push, delete data, or change production configuration
  without an explicit approval in the current conversation.
- Do not use a host shell for code tasks; use the Hermes sandbox backend.
- If Docker is unavailable, stop. Never silently fall back to the host.
- Network access is available for clone and declared sources. It is not an
  egress firewall; never put credentials in prompts or repositories.

## Verification

- The task has a clean diff and fresh test output.
- The sandbox contains no credentials passed through by default.
- The final action needing external side effects has an approval preview.
