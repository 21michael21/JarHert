---
name: sandboxed-coding
description: Run coding or source-bounded research through the private queue with reviewable results and no forwarded secrets. The local runner uses a disposable Codex workspace by default.
---

# Sandboxed Coding

## When to use

Use for repository research, implementation, tests, code review, or bounded
web research that needs files or commands. Hermes queues the request locally;
a separately authorised Mac runner claims it over SSH and runs the sandbox.
Use `mcp_jarhert_native_coding_job_list` to report the actual queue/result,
not an imagined completion.

For a clear coding or research request, call
`mcp_jarhert_native_coding_job_enqueue_confirmed` directly. Do not compose a separate preview in chat. The native tool owns the one approval prompt.

When the user explicitly asks for a deterministic sequence such as “сначала
проверь, потом покажи diff и итог”, pass the later instructions as `followups`.
They enter the same durable queue, start only after the preceding step succeeds,
receive its result as context, and produce one final Telegram report. Do not
create a second conversational plan or ask for another confirmation.

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

1. Work only in the disposable task workspace or an isolated git worktree.
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
- Use the Codex `workspace-write` sandbox by default. Do not use the dangerous
  bypass flag and do not pass application secrets into the runner.
- The optional Hermes Docker worker must be selected explicitly; never silently
  fall back to a general host shell.
- Network access is available for clone and declared sources. It is not an
  egress firewall; never put credentials in prompts or repositories.

## Verification

- The task has a clean diff and fresh test output.
- The sandbox contains no credentials passed through by default.
- The final action needing external side effects has an approval preview.
