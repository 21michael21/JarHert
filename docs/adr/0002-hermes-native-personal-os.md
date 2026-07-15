# ADR 0002: Hermes-native personal assistant

## Статус

Accepted. Hermes is the only JarHert runtime.

## Контекст

JarHert has useful domain code: reminders, notes, contacts, queue semantics,
Task Command Center integration, and tests. It also duplicates capabilities
that Hermes already provides: a Telegram gateway, sessions, memory, skills,
cron, subagents, sandbox backends, and approvals.

Running both gateways for one Telegram bot creates two sources of truth for
conversation state, scheduled delivery, authorization, and tool execution.

## Решение

Hermes becomes the only long-running agent runtime and the only Telegram
gateway. This repository becomes the versioned Hermes profile:

```text
Telegram
  -> Hermes gateway
  -> Hermes sessions, memory, cron, skills and sandbox
  -> narrow local Personal OS CLI skills
  -> SQLite and explicit external adapters
```

The Personal OS CLI is not a second agent and does not own a gateway. It is a
small local tool boundary for notes, projects, contacts, review data, and
carefully scoped Trello/Calendar adapters.

## Ownership

| Concern | Owner |
| --- | --- |
| Telegram updates, sessions, cron, subagents, skill discovery | Hermes |
| Personal profile and procedural skills | Hermes memory and skills |
| Notes, projects, contacts, links and search | Personal OS SQLite |
| Trello and Google Calendar credentials | Task Command Center external prerequisite |
| Code and research work | Hermes Docker sandbox workspace |

## Guardrails

- Do not start a second Telegram polling process with the Hermes bot token.
- Hermes is configured with explicit Telegram allowlists or pairing; never an
  open gateway.
- Skills may request actions through narrow local CLIs. They do not receive
  raw access to `.env`, SSH keys, Docker socket, or arbitrary host files.
- Agent-created skills and memory writes are staged for review at first.
- Code work runs in a dedicated sandbox workspace. Host deployment requires a
  separate explicit approval.

## Consequences

The old runtime is removed from the distribution. All new work targets native
Hermes tools and the Personal OS database. Deployment requires the native
release gate and an explicit live proof for Telegram, reminder, Calendar,
Trello and restart recovery.
