# JarHert

JarHert is a versioned Hermes profile for a personal Telegram assistant. Hermes
owns the Telegram conversation, memory, cron, skills and sandbox. JarHert adds
a small Personal OS: notes, projects, contacts, reminders, monitors, a private
coding queue and narrow adapters for Trello and Google Calendar.

The profile has one production runtime and one Personal OS SQLite database.
There is no second Telegram service, public queue API or application database.

## What it can do

- keep notes, projects, commitments, contacts and a searchable knowledge archive;
- create, list, move and cancel reminders;
- show a daily brief and a weekly review;
- read Trello and Google Calendar through an external Task Command Center;
- prepare and schedule a message after one explicit confirmation;
- watch GitHub releases and approved sources with diff-first checks;
- export text-only Telegram chat history as a temporary TXT or JSONL document;
- queue coding or bounded research work for a Mac runner over private SSH;
- open a Telegram Mini App dashboard with tasks, calendar, notes and status.

## Layout

```text
Telegram
  -> Hermes gateway
  -> JarHert native MCP tools
  -> Personal OS SQLite
  -> Task Command Center / GitHub / owner-authorized Telegram export

Mac coding runner -- SSH --> private native coding queue on the profile
```

The tools never receive raw `.env` files, SSH keys, a Docker socket or general
host-shell access. Coding work runs in a disposable Codex workspace by default;
the older Hermes Docker sandbox is an explicit local fallback. Deployment always
remains a separate approval.

## Local checks

Create a private profile environment from `.env.example`; never commit it.
The fast native check covers the local tools and does not call Telegram, LLMs,
Trello or Calendar:

```bash
scripts/local_check.sh
```

The release gate runs that check first. It deliberately skips external proof
unless it is explicitly enabled:

```bash
scripts/native_release_gate.sh
NATIVE_RELEASE_ALLOW_LIVE=1 scripts/native_release_gate.sh
```

The live mode sends real Telegram messages and creates then removes a small
Trello and Calendar canary. Run it only against the owner profile.

## Fast, quiet conversations

Telegram uses Hermes `interrupt` input mode: when you replace a question while
the assistant is working, the newer request wins instead of waiting behind an
old reply. The JarHert sync migrates only its previous `queue` setting; any
other explicit live display choice stays untouched.

For a voice dump, say everything naturally. Hermes first applies the compact
owner vocabulary, keeps clear items, and shows one preview for the whole set
of tasks, meetings, reminders and notes. Add a recurring correction once, for
example a project or name, through `voice_vocabulary_add`; it is then reused
without changing unrelated words.

For code or research, one request may include a deterministic continuation:
“сначала найди причину, потом проверь diff и напиши итог”. The first step and
its follow-ups share one durable chain, run in order, and send only the final
useful report. Replaying the same Telegram update keeps the same jobs and
cannot create a second chain.

## Hermes profile on a VPS

Install Hermes separately, then copy the profile through the guarded sync
script. It requires local `HEAD` to match `origin/main` and makes a profile
rollback copy before changing any owned files.

```bash
export JARHERT_VPS=deploy@your-vps-host
deploy/vps/sync_hermes_profile.sh
```

The profile environment lives at:

```text
~/.hermes/profiles/jarhert/.env
```

Set its permissions to `600`. Use the variables documented in `.env.example`.
Do not copy API keys into this repository or a systemd unit.

`deploy/vps/verify_single_telegram_gateway.sh` is a retirement guard: it fails
if an old competing Telegram process is still holding the bot token. The guard
can stop a specifically named obsolete systemd unit only when you opt in with
`RETIRE_LEGACY_GATEWAY=1` and provide the exact unit name.

## Task Command Center

Trello and Google Calendar are external prerequisites. Keep their code and
credentials outside this repository, for example:

```text
/opt/task-command-center
```

In the private profile environment:

```env
TASK_COMMAND_CENTER_DIR=/opt/task-command-center
TASK_COMMAND_CENTER_PYTHON=.venv/bin/python
TASK_COMMAND_CENTER_TIMEOUT_SECONDS=45
TASK_COMMAND_CENTER_HEALTH_CACHE_SECONDS=30
```

### MCP bundles

`HERMES_TOOL_SURFACE=catalog` is the recommended production surface. Hermes
starts with four small tools: status, integrations, discovery and a strict
catalog dispatcher. `tool_catalog_discover` returns the few relevant tools,
their required fields and their output contract; `tool_catalog_invoke` opens
that exact ability. This keeps the first prompt small without taking away any
native feature, approval or capability.

`HERMES_TOOL_SURFACE=full` retains the old one-tool-per-function MCP surface
for debugging and backwards compatibility. `HERMES_TOOL_BUNDLES=all` keeps the
complete catalog behind either surface. A focused profile may deliberately
expose only `personal`, `planning`, `research` or `code` (comma-separated);
operational status tools stay available. Change either variable between
sessions and restart Hermes afterwards.

`tool_catalog_discover` ищет небольшой подходящий набор по смыслу запроса и
возвращает входной и выходной контракт каждого инструмента. Он не меняет права
и не скрывает возможности от владельца. Для долгих plan вместо сырого объекта
используй `action_plan_trace`: там только статус, счётчики, следующий шаг и
реальные проблемы.

`memory_context` читает не больше 12 записей памяти, добавляет короткое summary
и помечает факты старше 90 дней. Это подсказка для контекста, а не источник
команд и не автоматическое сохранение новых данных.

JarHert invokes the allowlisted `taskctl.py` commands from that directory. Its
Trello token and Google OAuth files remain owned by Task Command Center. Check
the connection without changing anything:

```bash
HERMES_HOME=~/.hermes/profiles/jarhert \
  ~/.hermes/profiles/jarhert/.venv/bin/python \
  ~/.hermes/profiles/jarhert/native_tools/cli.py integration-health
```

## Dashboard

The Dashboard is a local FastAPI process. Bind it to loopback and publish it
only through an HTTPS reverse proxy before making it a Telegram menu button.

```bash
deploy/vps/install_dashboard_service.sh
```

Required private settings:

```env
JARHERT_DASHBOARD_SESSION_SECRET=<long-random-secret>
JARHERT_DASHBOARD_ALLOWED_TG_USER_IDS=<your-telegram-id>
```

It shows the Personal OS state and offers one-preview actions. It is not an
unauthenticated public admin panel.

## Coding and research runner

The VPS stores job metadata in Personal OS SQLite. A Mac claims work through
SSH, runs it in a disposable local Codex workspace and posts the result back to
the same queue. No HTTP port or service token is involved. Codex uses the
ChatGPT login already present on the Mac; its normal API key is not required.

```bash
.venv/bin/python scripts/setup_coding_profile.py
.venv/bin/python scripts/coding_runner.py \
  --queue-ssh deploy@your-vps-host \
  --worker-id mac-main --check
```

Remove `--check` to poll the queue. Add `--once` for one iteration. If the Mac
goes offline, the job lease expires and another configured runner may claim it.
Set `HERMES_CODING_EXECUTOR=hermes` or pass `--executor hermes` only when you
want the older Docker-backed Hermes runner instead.

## GitHub research

Public repository links work without a token: JarHert reads repository metadata,
the root tree and a short README excerpt through the GitHub public API. A
five-minute process cache avoids wasting the anonymous rate limit.

For private repositories, actions, issues, pull requests or security findings,
install the official GitHub MCP binary and use a fine-grained read-only token.
The profile enables only `repos`, `issues`, `pull_requests`, `actions`, `users`
and `code_security` with read-only lockdown.

## Telegram text exports

The owner can request a text-only export for a chat accessible to the separate
owner-authorized MTProto session. JarHert sends the resulting TXT or JSONL as a
Telegram document; it does not copy the export into memory or a coding workspace.
By default it deletes server-side files after 48 hours.

When the owner asks to read or summarize that export, Hermes can read a bounded
sample from the temporary file and answer from its contents. When the owner asks
for a deep analysis, Hermes shows one preview and queues the sample for the
isolated Mac research runner. The raw sample is cleared from the queue when that
job finishes. The runner may be configured to use Codex CLI, but that local CLI
must be installed and healthy before it can execute queued work.

Install the cleanup timer on the VPS:

```bash
export JARHERT_VPS=deploy@your-vps-host
deploy/vps/install_telegram_export_cleanup_timer.sh
```

## Backups and status

The encrypted profile backup and its restoration check are separate from the
normal Hermes runtime. Configure a recovery passphrase outside the VPS, then
install the timer:

```bash
deploy/vps/install_backup_timer.sh
```

`/status` reports safe operational information: integration health, worker
heartbeats, queue counts, backups and last error categories. It never displays
chat text, tokens or raw provider responses.

## Development boundaries

- `hermes/` is the shipped profile and native tool implementation.
- `deploy/vps/` contains explicit VPS sync and timer installers.
- `scripts/` contains native checks and local runner helpers.
- Private data, profile `.env`, Telethon sessions, SQLite data and exports stay
  outside version control.

For new features, add a narrow native tool and a focused test first. Keep
deterministic work deterministic; call an LLM only when a real change needs
interpretation.
