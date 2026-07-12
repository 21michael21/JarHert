# Telegram AI Brooch

> Migration direction: Hermes Agent becomes the only production Telegram
> runtime. The Python gateway documented below is legacy during the migration
> and must not run beside `hermes gateway` with the same bot token. The native
> profile, skills, and ownership boundary are in
> `docs/adr/0002-hermes-native-personal-os.md`.

## Hermes-native profile

The repository now contains Hermes skills for a personal operating center,
personal memory, and sandboxed coding. They are ordinary Hermes skills, not a
second bot or service.

Install the native profile from this checkout:

```bash
hermes profile install ./hermes --name jarhert --alias --yes
jarhert skills list
```

`SOUL.md`, profile config and skills are updated from the repository. Hermes
keeps API keys, Telegram token, memories, sessions and runtime databases in the
local profile and never takes them from git.

### Safe profile sync on VPS

The profile is a runtime, not a checkout: its `.env`, `auth.json`, SQLite
databases, Telegram session, cron state, logs and user-created skills must stay
on the server. Update only versioned profile assets from a clean, pushed
JarHert `main`:

```bash
JARHERT_VPS=deploy@your-vps-host deploy/vps/sync_hermes_profile.sh
```

The script creates a profile-asset rollback copy, updates `SOUL.md`, `AGENTS.md`,
skills, native tools and scripts, installs missing native dependencies, restarts
the one Hermes gateway and verifies that it is active. It deliberately preserves
the live provider configuration, such as Codex OAuth, while merging newly
versioned native MCP tools into its allowlist. Set `SYNC_PROFILE_CONFIG=0` to
skip even that merge, or `SYNC_PROFILE_CONFIG=1` only when you have reviewed the
provider change and want to replace the full `config.yaml`.

During every sync, broad host-level toolsets are disabled for Telegram:
terminal, file access, browser automation, code execution, computer use,
delegation and built-in cron. Telegram keeps the native JarHert MCP tools,
skills, memory and web search. Task and Calendar mutations therefore go through
the single approval-plan path; coding stays in the separate sandbox runner.

The managed source clone on the server is `/home/deploy/jarhert-profile` by
default. It is pinned to the exact commit pushed to `origin/main`; the upstream
Hermes Agent clone is never pulled or reset by this command.

### Локальный голосовой inbox

Профиль включает бесплатное локальное распознавание Telegram-голосовых через
`faster-whisper` с моделью `base`. Оно работает на VPS, не использует API-ключ
и не отправляет аудио стороннему провайдеру. Сырая расшифровка не дублируется в
чате: одна голосовая даёт один нормальный ответ или один план действий.

На сервере с 2 CPU и 4 GB RAM оставь модель `base` и не запускай несколько
голосовых параллельно. Первый запрос может быть медленнее: Hermes загрузит
модель в память. Не меняй `stt.provider` на облачный сервис, если цель —
оставить распознавание бесплатным и локальным.

### Task Command Center sync for the native Hermes profile

Task Command Center stays outside JarHert Git because its local folder contains
Trello credentials and Google OAuth files. The native profile invokes it as a
local adapter, so copy it to the same VDS separately and only with an explicit
secret-copy flag:

```bash
TASK_COMMAND_CENTER_SOURCE=/absolute/path/to/task-command-center \
JARHERT_VPS=deploy@your-vps-host \
TASK_COMMAND_CENTER_COPY_SECRETS=1 \
deploy/vps/sync_task_command_center.sh
```

The command sends the source code and its `.env`, `client_secret.json` and
`token.json` directly to `/home/deploy/task-command-center`, creates an isolated
venv there, applies mode-600 permissions to secrets, updates only the two TCC
path variables in the live Hermes profile, then performs read-only Trello and
Calendar health checks. It does not print, commit or copy those secrets into
the JarHert profile distribution.

### Encrypted backup, rotation and restore proof

`hermes/scripts/backup_profile.py` makes an SQLite-consistent snapshot of the
Hermes profile state, encrypts it with local GnuPG AES-256, and rotates old
archives: seven daily, four weekly and three monthly points by default. It backs
up the profile databases plus configuration and session files needed for
recovery. Archives are never committed to Git.

Keep the recovery secret in your password manager before entering it on the
VPS. The VPS keeps a mode-600 systemd environment file solely so the daily
timer can encrypt backups automatically. Configure it interactively without
printing the secret:

```bash
python ~/.hermes/profiles/jarhert/scripts/configure_backup_secret.py
```

The helper asks twice, rejects weak or malformed values, refuses to overwrite
an existing file unless `--replace` is explicit, and never echoes the phrase.

Create an archive and immediately prove it can be restored without touching the
live profile:

```bash
set -a
. ~/.config/jarhert/backup.env
set +a
python ~/.hermes/profiles/jarhert/scripts/backup_profile.py backup
python ~/.hermes/profiles/jarhert/scripts/backup_profile.py verify --archive ~/.hermes/backups/jarhert/<archive>.tar.gpg
```

The encrypted archive directory should also be copied to storage outside the
VPS. A VPS-local archive protects from a bad deploy; it does not protect from a
lost server. Do not enable a paid provider snapshot without checking its
retention and restore policy.

Install the daily timer after syncing the profile:

```bash
JARHERT_VPS=deploy@your-vps-host deploy/vps/install_backup_timer.sh
```

The timer runs at `03:15` and verifies every new archive by restoring it into a
temporary directory. It stays safely skipped until `~/.config/jarhert/backup.env`
exists with mode `600`; this prevents accidental unencrypted backups.

### Gateway watchdog

`hermes/scripts/watchdog.py` checks the user systemd gateway, its main PID,
disk headroom, memory pressure and zombie children. Zombie processes do not
consume CPU or RAM themselves, so the watchdog reports them instead of blindly
killing processes. The installed timer restarts only an inactive gateway; it
never restarts a healthy process.

```bash
python ~/.hermes/profiles/jarhert/scripts/watchdog.py
```

Use its JSON output in a systemd timer or Hermes no-agent cron. The future
`/status` command will surface the same operational data in Telegram.

Install the user-level timer after profile sync:

```bash
JARHERT_VPS=deploy@your-vps-host deploy/vps/install_watchdog_timer.sh
```

It runs every five minutes and keeps its result in the user journal. For an
intentional maintenance window, create
`~/.hermes/profiles/jarhert/state/maintenance` before stopping the gateway.
Remove the marker and start the gateway again when the work is finished. The
timer does not restart a healthy gateway and does not need access to any secret.

The distribution config keeps an API-compatible development fallback. Profile
sync deliberately preserves the live server's `model` settings, including a
Codex OAuth subscription profile, so a source update cannot silently switch it
back to an API key. Free gateways are not an automatic primary route because
their availability and model selection are not predictable enough for reminders
and external actions.

Install the lightweight native dependency used for owner-authorized Telegram
text export:

```bash
~/.hermes/hermes-agent/venv/bin/python \
  ~/.hermes/profiles/jarhert/scripts/bootstrap_native_deps.py
```

Expected native skills:

- `personal-operating-center` — plan today, triage inbox, evening review;
- `personal-memory` — notes, promises, projects, and contacts;
- `contact-messaging` — one preview and one confirmation for a complete
  scheduled Telegram message plan;
- `event-monitors` — deterministic hash/diff checks before any model call;
- `skill-distillation` — three confirmed successful repeats become one staged
  procedural skill;
- `tasks-calendar` — Trello and Google Calendar reads plus one-confirmation
  mutation plans;
- `telegram-chat-export` — confirmed text-only TXT/JSONL export from a dialog
  accessible to the owner's MTProto account;
- `sandboxed-coding` — repository work only in an isolated workspace.

Skill distillation replaces Hermes' generic tool-iteration nudge for this
profile. An approval before a task does not count. The result must be successful
and separately confirmed as useful three times with distinct update IDs. The
resulting `SKILL.md` is staged through `skill_manage`, because
`skills.write_approval` is enabled. Review it with `/skills pending` and
`/skills diff <id>` before applying it.

### Sandboxed coding and research

The main chat keeps the local backend so ordinary Personal OS commands stay
lightweight. Coding and bounded research use `personal-os sandbox run`, which
starts the same `jarhert` profile with `TERMINAL_ENV=docker`. There is no second
bot or second agent identity.

The Docker workspace is ephemeral and limited to 1 CPU, 2 GB RAM, and 4 GB
disk. No environment variables are forwarded into it. Coding accepts a GitHub
HTTPS repository; research accepts sources whose hosts are explicitly listed
in `HERMES_RESEARCH_ALLOWED_HOSTS`. Docker is a hard requirement: the worker
refuses to fall back to the host.

The container still has outbound network access for clone and research. The
host allowlist validates declared sources but is not a network firewall. Use a
proxy or Docker egress policy before handling hostile repositories.

### Native contacts and scheduled messages

Contacts and outgoing plans live in the profile SQLite database, not in a
second gateway. Add an exact contact name and aliases:

```bash
python ~/.hermes/profiles/jarhert/native_tools/cli.py contact add \
  --name "Илья" --telegram-chat-id 123456 --alias "Илье"
```

Hermes creates one plan for every message in the user's request, shows one
preview, and asks for one confirmation for the whole plan. One script-only cron
job delivers due messages without an LLM call:

```bash
hermes cron create "* * * * *" \
  --name "Personal OS message dispatcher" \
  --script dispatch_due_messages.py --no-agent --deliver local
```

Delivery requires the Hermes Telegram gateway to be configured and running.
Contact resolution is exact and alias-based; a similar name is never guessed.

### Личный архив ссылок

Попроси Hermes сохранить явно присланную публичную ссылку, а позже спроси по
содержимому: `сохрани эту страницу в Hub_ML` или `найди в сохранённом про OAuth`.
Профиль берёт одну HTTPS-страницу, очищает HTML до текста, сохраняет её в
локальной SQLite-базе и ищет по ней через FTS. Это не краулер: он не ходит по
ссылкам, не использует cookies/логин и не принимает внутренние адреса.

Для каждого URL сохраняются только изменившиеся версии, максимум 20 снимков.
Идентичная страница не занимает ещё одну копию. Внешние сайты не вызываются
сами по себе: архивирование требует одного подтверждения в Telegram.

### Покупки и бытовые дела

Для покупок пиши по-человечески: `добавь молоко и батарейки в покупки`,
`что купить`, `купил молоко` или `убери батарейки из покупок`. Список живёт в
локальной SQLite-базе, повтор того же Telegram update не создаёт дубль, а
«убери» только отменяет позицию, не стирая историю.

Повторяющиеся бытовые дела не получают второй параллельный список: используй
существующие напоминания, например `каждое воскресенье в 12 напомни поменять
фильтр`. Это даёт одно место для расписаний и уведомлений.

### Поездки

`Создай поездку в Амстердам с 10 по 14 мая` создаёт локальную карточку поездки.
Туда можно добавить маршрут, бронь, документ или checklist: `в поездку
Амстердам добавь бронь отеля, напомни 1 мая проверить её`. Срок пункта создаёт
обычное Telegram-напоминание. Hermes не покупает билеты, не входит в сервисы
бронирования и не отправляет маршрут кому-либо без отдельного плана.

### Native diff-first monitors

The first monitor source is GitHub Releases. The first successful check stores
a silent baseline. Equal payloads produce no event and no model call. A changed
payload creates a compact diff; only then may Hermes evaluate the user's
condition.

```bash
python ~/.hermes/profiles/jarhert/native_tools/cli.py monitor add \
  --name "codex-releases" \
  --source-type github_releases \
  --source-config-json '{"owner":"openai","repo":"codex"}' \
  --condition "Напиши только если в релизе есть важные возможности"

hermes cron create "every 30m" \
  --name "Personal diff monitors" \
  --script check_monitors.py --skill event-monitors --deliver origin
```

List monitors with `monitor list`. `monitor remove <id>` disables a monitor
without deleting its state. Arbitrary URLs, shell commands, and browser tools
are not accepted by this source adapter.

Keep credentials in `~/.hermes/.env`. Use Hermes Telegram pairing or an
explicit allowlist. Do not copy a token into this repository and do not start
the legacy `gateway_bot` once `hermes gateway` owns the bot token.

Отдельный Telegram AI-помощник на базе Hermes Agent.

Цель MVP: быстрый и безопасный помощник в Telegram, который отвечает через Hermes, сохраняет важное, ставит напоминания и не тратит бесплатные LLM-квоты на задачи, которые обычный код делает лучше.

## Архитектура

```text
Telegram
  -> gateway_bot
  -> local intent / limits / quality gates
  -> Hermes adapter
  -> Hermes Agent / providers / fallback
  -> output quality gates
  -> Telegram
```

Hermes используется как AI-runtime. Gateway остаётся владельцем безопасности, лимитов, памяти, напоминаний, очередей и диагностики.

## MVP-команды

- `/ask текст` — спросить AI.
- обычный текст — тоже вопрос к AI, если `AI_REPLY_TO_PLAIN_TEXT=true`.
- `/idea текст` — записать идею.
- `/ideas` — показать последние идеи.
- `/remember текст` — сохранить важную заметку.
- `/memories` — показать сохранённое.
- `/remind через 2 часа текст` — создать напоминание.
- `/reminders` — показать активные напоминания.
- `/cancel_reminder id` — отменить напоминание.
- `/task название | list=Today | project=Personal | priority=P2 | due=2026-07-10` — создать Trello-карточку через Task Command Center.
- `/tasks Today` — показать Trello-карточки из списка.
- `/task_move название | to=In Progress` — переместить Trello-карточку.
- `/task_done название | summary=что сделано` — закрыть Trello-карточку в Done.
- `/calendar название | start=2026-07-10 10:00 | end=2026-07-10 10:30 | reminder=5` — создать Google Calendar событие.
- `/do цель` — поставить сложную агентскую задачу в очередь.
- `/jobs` — показать очередь агента.
- `/job id` — показать план и статус конкретной агентской задачи.
- `/status` — показать режим, лимиты и диагностику.
- `/admin_status` — диагностика владельца, только для `ADMIN_TG_USER_IDS`.

Естественные фразы тоже работают:

- `идея: сделать короткий чеклист`;
- `запиши идею сделать голосовой inbox`;
- `запомни проверить оплату сервера`;
- `напомни через 2 часа проверить деплой`.
- `создай задачу проверить Trello`;
- `покажи задачи Today`;
- `поставь в календарь созвон | start=2026-07-10 10:00 | end=2026-07-10 10:30`.
- `Гермес сделай: проверь Trello, календарь и покажи итог`.

Команды со слэшем нужны как ручной/debug-режим. Нормальный сценарий — писать как человеку:

- `завтра в 10 проверь сервер`;
- `завтра в 12 созвон с Ильей`;
- `запиши идею про Hub ML и напомни через 1 час обсудить`;
- `перенеси задачу проверить сервер в Done`;
- `закрой задачу проверить деплой`.

AI Brooch сначала пробует локальный `natural_router`: он строит список действий без LLM и без догадок. Если действия не найдены, текст уходит в обычный AI-ответ.

Natural UX 2.0 поддерживает более свободные формулировки:

- `сегодня вечером проверь деплой`;
- `завтра утром созвон с Ильей`;
- `напомни через полчаса позвонить`;
- `напомни до завтра отправить отчет`;
- `в пятницу в 15 демо проекта`;
- `на этой неделе подготовь план запуска`;
- `что у меня сегодня`;
- `покажи план на завтра`;
- `задача один проверить сервер в 10:00, задача два созвон в 12:00`.

Если deterministic router не понял действие, но фраза похожа на просьбу что-то сделать, включается LLM JSON extractor. Он не исполняет действия сам: только возвращает JSON-план, который проходит schema validation и safety checks. Если уверенность низкая, бот просит уточнить.

Можно без команд, одним сообщением или голосом:

```text
завтра задача 1 проверить сервер в 10:00, задача 2 созвон с Ильей в 12:00, задача 3 Hub ML в 15:30
```

Бот разложит это на отдельные Trello-карточки. Если у пункта есть время, он добавит календарный блок на 30 минут. По умолчанию список Trello — `Today`, проект — `Personal`, приоритет — `P3`.

Голосовые сообщения расшифровываются через `OPENAI_TRANSCRIBE_MODEL`, затем выполняются как обычный текст. Например голосом можно сказать: `напомни через 30 минут позвонить`.

## Google Docs для идей

В MVP бот всегда сохраняет идеи и напоминания локально в SQLite. Если нужно дополнительно писать их в Google Docs, подключи webhook:

```env
GOOGLE_DOCS_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
GOOGLE_DOCS_WEBHOOK_TOKEN=
GOOGLE_DOCS_WEBHOOK_TIMEOUT_SECONDS=5
```

Бот отправляет `POST` с JSON:

```json
{
  "kind": "idea",
  "user_id": 1,
  "text": "текст идеи",
  "created_at": "2026-07-09T00:00:00+00:00"
}
```

`kind` бывает `idea` или `reminder`. Webhook должен сам проверить `Authorization: Bearer <GOOGLE_DOCS_WEBHOOK_TOKEN>` и добавить строку/абзац в нужный документ. Если webhook недоступен, запись всё равно остаётся в локальной базе.

Для Google Apps Script смотри готовую инструкцию: `docs/google-docs-webhook.md`.

Если нужен не webhook, а прямая интеграция через уже знакомый service account-подход из старого `interview-reviewer`, включи Google Sheets sync:

```bash
.venv/bin/pip install -e ".[google]"
```

```env
ENABLE_GOOGLE_SHEETS_SYNC=true
GOOGLE_SPREADSHEET_ID=
GOOGLE_ASSISTANT_SHEET_NAME=AI Brooch
GOOGLE_PROJECT_ID=
GOOGLE_PRIVATE_KEY_ID=
GOOGLE_PRIVATE_KEY=
GOOGLE_CLIENT_EMAIL=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_X509_CERT_URL=
```

Таблица должна быть расшарена на `GOOGLE_CLIENT_EMAIL`. Бот сам создаст лист `AI Brooch` и колонки `created_at`, `kind`, `record_id`, `user_id`, `text`.

Приоритет sync:

1. `ENABLE_GOOGLE_SHEETS_SYNC=true` — прямая запись в Google Sheets.
2. Если Sheets выключен, но задан `GOOGLE_DOCS_WEBHOOK_URL` — webhook.
3. Если ничего не задано — только локальная SQLite.

Для голосовых:

```env
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
VOICE_MAX_BYTES=10485760
TELEGRAM_BLOCKING_MAX_CONCURRENCY=4
TELEGRAM_BLOCKING_TIMEOUT_SECONDS=60
TELEGRAM_FAST_ACK_SECONDS=0.6
```

`TELEGRAM_BLOCKING_MAX_CONCURRENCY` ограничивает суммарное число LLM, STT и Task Command Center вызовов. Сообщения одного пользователя выполняются по порядку, а разные пользователи не блокируют друг друга. Если работа не укладывается в `TELEGRAM_FAST_ACK_SECONDS`, бот быстро отправляет «Принял» и доставляет итог через outbox.

## Native coding runner

VDS хранит очередь внутри профиля Hermes, а Mac выполняет код в своём Docker
sandbox. Между ними нет публичного HTTP-порта, второго backend-сервиса или
общего API-токена: runner вызывает только фиксированный profile CLI через твой
SSH-ключ.

На Mac из checkout JarHert:

```bash
.venv/bin/python scripts/coding_runner.py \
  --queue-ssh deploy@your-vps-host \
  --worker-id mac-main
```

Для одной итерации добавь `--once`. Runner атомарно забирает lease, шлёт
heartbeat и возвращает ограниченный результат в очередь. Если Mac выключился,
job после lease снова доступна следующему runner. Старый HTTP-режим остаётся
только для legacy-развёртывания с `JARHERT_BACKEND_URL`.

Перед первым запуском без LLM-вызова проверь весь путь:

```bash
.venv/bin/python scripts/coding_runner.py \
  --queue-ssh deploy@your-vps-host --worker-id mac-main --check
```

Команда проверяет private SSH queue, Docker и локальный профиль Hermes, но не
забирает job, не запускает модель и не создаёт внешний эффект.

Script-only Hermes cron забирает завершённые jobs и присылает короткий итог в
чат владельца. Он не вызывает LLM и при временной ошибке Telegram вернёт
результат в очередь на повторную доставку.

## Personal export

Команда `/export_me` присылает владельцу ZIP с `account.json`, консистентной
копией Personal OS SQLite и manifest SHA-256. Перед отправкой архив автоматически
проверяется; временная серверная копия удаляется после Telegram upload.

Для безопасной ручной проверки и восстановления в отдельный каталог используй
`PersonalExportService.verify()` и `restore()`. Restore не перезаписывает живую
production DB. Полный серверный backup/restore остаётся отдельным обязательным
операционным процессом.

Команда `/status` показывает безопасную сводку: AI provider, Trello/Calendar
OAuth, heartbeat workers, собственные очереди, delivery, queue lag и последние
типы ошибок. Сырые ответы провайдеров, тексты задач и секреты не выводятся.

## Trello и Google Calendar через Task Command Center

AI Brooch может использовать уже настроенный соседний проект:

```text
/opt/task-command-center
```

Путь не зашит в код. AI Brooch берёт его только из `TASK_COMMAND_CENTER_DIR`. Если `TASK_COMMAND_CENTER_ENABLED=true`, но `TASK_COMMAND_CENTER_DIR` пустой или каталог не существует, `scripts/preflight.py` завершится ошибкой.

Task Command Center — внешний prerequisite. Его код не лежит в этом репозитории. В нём лежат его собственные секреты: Trello `.env`, Google OAuth `client_secret.json` и `token.json`. AI Brooch не копирует эти секреты и не печатает их, а вызывает `taskctl.py` как локальный tool из каталога, указанного в env.

Настройки:

```env
TASK_COMMAND_CENTER_ENABLED=true
TASK_COMMAND_CENTER_DIR=/opt/task-command-center
TASK_COMMAND_CENTER_PYTHON=.venv/bin/python
TASK_COMMAND_CENTER_TIMEOUT_SECONDS=45
TASK_COMMAND_CENTER_HEALTH_CACHE_SECONDS=30
```

Внутри одного Hermes MCP-процесса health-check кэшируется на этот TTL. Изменения
Trello и Calendar из одного подтверждённого плана выполняются одним batch-процессом:
клиенты создаются один раз, а Google OAuth service переиспользуется до завершения
плана. Отдельные действия по-прежнему получают собственный success/error результат.

Проверка соседнего проекта:

```bash
cd "$TASK_COMMAND_CENTER_DIR"
.venv/bin/python taskctl.py list --list Today
.venv/bin/python taskctl.py calendar-test
```

Проверка из AI Brooch без создания календарных событий:

```bash
.venv/bin/python - <<'PY'
from gateway_bot.main import build_task_center
health = build_task_center().health_check()
print(f"trello_ok={health.trello_ok} {health.trello_detail}")
print(f"calendar_ok={health.calendar_ok} {health.calendar_detail}")
PY
```

Полный preflight/health-check перед запуском:

```bash
.venv/bin/python scripts/preflight.py
```

Текущий статус:

- Trello real API работает: список `Today` читается.
- Google Calendar OAuth сейчас проходит health-check через `list_today_events()`.
- Если OAuth снова протухнет, обнови `token.json` так:

```bash
cd "$TASK_COMMAND_CENTER_DIR"
mv token.json token.json.bak
.venv/bin/python taskctl.py calendar-test
```

После browser OAuth команда `/calendar ...` в Telegram начнёт создавать события.

## Deployment on VPS

Task Command Center запускается не как отдельный демон, а как локальный скрипт: AI Brooch вызывает `taskctl.py` дочерним процессом. На VPS держи JarHert и Task Command Center в разных каталогах.

Пример структуры:

```text
/opt/jarhert
/opt/task-command-center
```

В `/opt/jarhert/.env`:

```env
TASK_COMMAND_CENTER_ENABLED=true
TASK_COMMAND_CENTER_DIR=/opt/task-command-center
TASK_COMMAND_CENTER_PYTHON=.venv/bin/python
TASK_COMMAND_CENTER_TIMEOUT_SECONDS=45
```

Секреты Task Command Center лежат в `/opt/task-command-center`, не в JarHert:

```text
/opt/task-command-center/.env
/opt/task-command-center/client_secret.json
/opt/task-command-center/token.json
```

Не храни рядом backup-файлы OAuth token вроде `token.json.bak*` дольше, чем нужно для ручного восстановления. Это такие же секреты, как основной `token.json`.

Права:

```bash
sudo chown -R jarhert:jarhert /opt/jarhert /opt/task-command-center
sudo chmod 600 /opt/jarhert/.env /opt/task-command-center/.env /opt/task-command-center/client_secret.json /opt/task-command-center/token.json
```

Systemd-вариант для polling-бота:

```ini
[Unit]
Description=JarHert Telegram bot
After=network-online.target

[Service]
User=jarhert
WorkingDirectory=/opt/jarhert
EnvironmentFile=/opt/jarhert/.env
ExecStart=/opt/jarhert/.venv/bin/python -m gateway_bot.telegram_app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Docker-вариант: смонтируй Task Command Center в контейнеры backend и bot тем же путём, который указан в `TASK_COMMAND_CENTER_DIR`.
В `docker-compose.yml` mount оставлен как закомментированный пример, чтобы локальный запуск не требовал `/opt/task-command-center`. На VPS раскомментируй его для `backend` и `bot`.

```yaml
services:
  backend:
    volumes:
      - ./data:/app/data
      - /opt/task-command-center:/opt/task-command-center:ro
  bot:
    volumes:
      - ./data:/app/data
      - /opt/task-command-center:/opt/task-command-center:ro
```

Проверка на сервере:

```bash
cd /opt/jarhert
set -a
. ./.env
set +a
cd "$TASK_COMMAND_CENTER_DIR"
.venv/bin/python taskctl.py list --list Today
.venv/bin/python taskctl.py calendar-test
cd /opt/jarhert
.venv/bin/python scripts/preflight.py
```

## Telegram polling локально

```bash
cp .env.example .env
# заполни BOT_TOKEN
# опционально: ALLOWED_TG_USER_IDS=123456789
.venv/bin/pip install -e ".[dev]"
scripts/run_local_bot.sh
```

Если `ALLOWED_TG_USER_IDS` пустой, локально бот отвечает всем. Для сервера allowlist обязателен.

Если нужно временно взять токен из локального проекта Telegram Library:

```bash
.venv/bin/python scripts/import_telegram_library_env.py --dry-run
.venv/bin/python scripts/import_telegram_library_env.py
```

Скрипт копирует только `BOT_TOKEN`, не печатает значение и не трогает базу читалки. Не запускай одновременно два polling-процесса на одном токене.

Если это токен бота читалки (`@biba_book_bot`), есть два безопасных режима:

- локальный тест ассистента: временно не запускать polling читалки на этом токене;
- продакшн: либо отдельный bot token для ассистента, либо объединение обработчиков читалки и ассистента в один bot-process.

Два независимых polling-сервиса на одном `BOT_TOKEN` будут конфликтовать.

Для режима "один чат с читалкой, но AI-брошка отдельным сервисом" используй HTTP endpoint ассистента. Тогда Telegram Library bot остаётся единственным процессом, который получает Telegram updates, а AI-сервис только отвечает на внутренние запросы:

```http
POST /api/assistant/telegram-text
Authorization: Bearer <ASSISTANT_SERVICE_TOKEN>
Content-Type: application/json

{"tg_user_id":123,"text":"/ask коротко объясни главу"}
```

Ответ:

```json
{
  "text": "ответ пользователю",
  "intent": "ask",
  "provider": "fake",
  "model": "fake-model",
  "fallback_count": 0,
  "blocked_reason": null
}
```

`ASSISTANT_SERVICE_TOKEN` обязателен. Без него endpoint возвращает `401`.

Локальный fake-режим уже полезен:

- `/ask` проверяет полный gateway path без внешних API;
- `/remember` сохраняется в SQLite;
- `/remind` сохраняется в SQLite;
- reminder worker отправляет due reminders во время polling.

По умолчанию SQLite лежит в `data/ai_brooch.sqlite3`.

## Очередь агента

Очередь агента — это безопасный фундамент для «Джарвиса». Сейчас она делает четыре вещи:

1. принимает цель через `/do`;
2. сохраняет её в SQLite как `agent_jobs`;
3. строит короткий план шагов и показывает статус через `/jobs` и `/job id`.
4. для тяжёлых allowlist-действий (`task.*`, `calendar.*`) отдаёт быстрый ответ `Принял, выполняю. Job #...`, кладёт action в очередь и присылает итог отдельным сообщением через delivery outbox.

Пример:

```text
/do разложи завтра задачи по Trello, добавь слоты в календарь и покажи итог
```

Ответ будет вида:

```text
Поставил в очередь job #1.
Статус: queued
План:
1. Зафиксировать цель и ограничения.
2. Создать или обновить задачу в Trello через Task Command Center.
3. Создать календарный блок, если указан срок или время.
4. Показать итог и следующие действия.
Проверить: /job 1
```

Важно: очередь выполняет только allowlist-инструменты из `assistant/tool_registry.py`. Shell, произвольные файлы и серверные команды туда не входят.

## Docker локально

```bash
cp .env.example .env
# заполни BOT_TOKEN
docker compose build
docker compose up -d backend
curl -fsS http://localhost:8000/health
docker compose up -d bot
docker compose logs -f bot
```

Остановить:

```bash
docker compose down
```

Данные SQLite остаются в `./data`.

## Локальный запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
scripts/local_check.sh
.venv/bin/python scripts/local_smoke.py
.venv/bin/python scripts/preflight.py
```

На первом этапе реальный Hermes не нужен: тесты используют fake-клиент.

`preflight.py` ожидаемо завершится ошибкой, если в `.env` ещё нет `BOT_TOKEN`. Это нормальная проверка перед реальным Telegram polling.

## Диагностика и readiness

- `GET /health` подтверждает, что backend запущен.
- `GET /readyz` дополнительно проверяет, что схема базы данных применена; при проблеме возвращает `503`.
- `/admin_status` у владельца показывает состояние провайдеров, delivery outbox, worker heartbeat и p50/p95 для provider, queue и delivery.
- `/trace <trace_id>` показывает безопасный путь Telegram update → intent/provider → job/action/tool → outbox/delivery. В trace не выводятся тексты сообщений, prompt'ы, цели job, API keys и сырые ошибки провайдеров.

## Live e2e перед тестом с друзьями

### Строгий system E2E

`scripts/live_system_e2e.py` всегда пишет JSON-отчёт в `reports/live-system-e2e/`. В отчёте есть
`trace_id`, latency, статус и безопасные metadata каждого шага. Содержимое сообщений и секреты в
отчёт не попадают.

Локальный режим использует настоящие pipeline, SQLite stores, workers, approval callback, monitor
runner и delivery outbox. Подменяются только LLM, Telegram, Trello/Calendar и STT:

```bash
.venv/bin/python scripts/live_system_e2e.py --mode local --tg-user-id <telegram_id>
```

Sandbox использует реальный настроенный LLM и, при наличии `--voice-file`, реальный STT. Записи в
Trello/Calendar и сообщения в Telegram не создаются:

```bash
.venv/bin/python scripts/live_system_e2e.py \
  --mode sandbox \
  --tg-user-id <telegram_id> \
  --voice-file dev/voice_fixtures/live_e2e.m4a
```

Live идёт через отдельную авторизованную Telethon user-session в реально запущенный бот. Бот и его
action/reminder/outbox workers должны уже работать с production-like конфигурацией:

```bash
scripts/create_voice_fixture.sh "создай задачу проверить системный тест"
TELEGRAM_API_ID=... TELEGRAM_API_HASH=... \
.venv/bin/python scripts/live_system_e2e.py \
  --mode live \
  --require-live \
  --tg-user-id <telegram_id> \
  --voice-file dev/voice_fixtures/live_e2e.m4a
```

Для strict voice-сценария аудио должно содержать действие, которое создаёт approval-кнопку. Иначе
STT будет проверен, но полный путь voice → action queue → approval → outbox корректно завершится
ошибкой.

Для первого запуска Telethon-сессию авторизуй отдельно; строгий runner сам не запрашивает код и не
зависает в CI. Путь задаётся через `LIVE_E2E_TELETHON_SESSION`. При `--require-live` любой skip,
blocked reply, fake provider, отсутствующий credential или timeout даёт ненулевой exit code.

Отдельный `scripts/live_hermes_e2e.py` обращается к реальному Hermes gateway, создаёт временные
Trello/Calendar сущности и может написать в Telegram. Он всегда требует явный флаг `--allow-live`:

```bash
HERMES_HOME=~/.hermes/profiles/jarhert \
~/.hermes/profiles/jarhert/.venv/bin/python scripts/live_hermes_e2e.py --allow-live
```

Проверяются text/LLM, voice/STT/natural route, task и Calendar с inline approval, reminder, итоговая
доставка outbox, provider fallback, action idempotency, восстановление queued action новым store,
monitor triggered/no-change и ownership. Текущая duplicate-проверка относится к action queue:
durable dedup входящих Telegram `update_id` в проекте пока не реализован и этим тестом не заявляется.

Важно: этот legacy system runner использует изолированный `LocalTaskCenter` в
component cycle. Поэтому он не доказывает нативные внешние Trello/Calendar
вызовы Hermes на VDS. Для них используй `operator_canary.py` ниже; для входящего
Telegram и voice нужен отдельный Telethon gateway scenario с явным username
тестируемого бота.

### Native operator canary

Для живого Hermes-профиля есть отдельный короткий canary. Он создаёт уникальные
временные Trello-задачу, Calendar-событие и reminder, посылает одно служебное
сообщение владельцу в Telegram, затем удаляет все три временные сущности даже
при ошибке delivery. Он не проверяет входящий Telegram update, LLM или STT и не
выдаёт себя за полный gateway E2E:

```bash
HERMES_HOME=~/.hermes/profiles/jarhert \
~/.hermes/profiles/jarhert/.venv/bin/python \
  ~/.hermes/profiles/jarhert/scripts/operator_canary.py --allow-external
```

Запускай его только для owner chat: флаг `--allow-external` обязателен именно
потому, что canary делает краткоживущие внешние изменения.

### Legacy component smoke

Скрипт `scripts/live_e2e.py` проверяет цепочку:

```text
Telegram user id -> GatewayService -> Pipeline -> LLM/provider
  -> reminder/action worker -> delivery outbox -> Telegram Bot API
```

По умолчанию он использует изолированную SQLite-базу `data/live_e2e.sqlite3`, не трогает основную очередь и не пишет в Google Docs/Sheets. Это безопасный режим для проверки логики:

```bash
ALLOWED_TG_USER_IDS= HERMES_MODE=fake TASK_COMMAND_CENTER_ENABLED=false \
  .venv/bin/python scripts/live_e2e.py --tg-user-id <твой_telegram_id>
```

Проверка очереди задач через Task Command Center:

```bash
.venv/bin/python scripts/live_e2e.py \
  --tg-user-id <твой_telegram_id> \
  --include-task
```

Проверка календаря через Task Command Center:

```bash
.venv/bin/python scripts/live_e2e.py \
  --tg-user-id <твой_telegram_id> \
  --include-calendar
```

Реальный прогон через Telegram Bot API:

```bash
.venv/bin/python scripts/live_e2e.py \
  --tg-user-id <твой_telegram_id> \
  --send-telegram \
  --require-real-llm \
  --include-task \
  --include-calendar
```

Условия:

- пользователь с `<твой_telegram_id>` уже открыл бот в Telegram;
- `BOT_TOKEN` задан в `.env`;
- для real LLM `HERMES_MODE` не должен быть `fake`;
- для task/calendar-проверки должен работать Task Command Center;
- для голосового сценария передай локальный аудиофайл:

```bash
.venv/bin/python scripts/live_e2e.py \
  --tg-user-id <твой_telegram_id> \
  --send-telegram \
  --voice-file /path/to/voice.oga
```

Если нужно проверить sync в Google Docs/Sheets, добавь `--allow-doc-sync`. Без этого флага live e2e специально выключает внешний sync, чтобы не засорять рабочие документы.

## Hermes adapter modes

```env
HERMES_MODE=fake
```

Локальный fake-клиент для тестов.

```env
HERMES_MODE=http
HERMES_API_URL=http://127.0.0.1:8765
HERMES_API_PATH=/api/chat
HERMES_API_TOKEN=
```

HTTP-адаптер. Он отправляет JSON с `prompt`, `message`, `session`, `metadata` и нормализует ответы форматов `text`, `response`, `message.content` и OpenAI-style `choices[0].message.content`.

```env
HERMES_MODE=cli
HERMES_CLI_COMMAND=hermes --oneshot {prompt}
```

CLI-адаптер. По умолчанию он вызывает Hermes one-shot и подставляет prompt в `{prompt}` без shell. Если placeholder не указан, адаптер сохраняет старый режим: передаёт prompt в stdin и берёт ответ из stdout.

Проверка установленного Hermes CLI без запуска Telegram polling:

```bash
HERMES_MODE=cli HERMES_CLI_COMMAND="hermes --oneshot {prompt}" \
  .venv/bin/python scripts/hermes_cli_check.py --timeout 35
```

Если Hermes установлен, но provider keys не настроены, checker должен завершиться с понятной ошибкой. Для бесплатного старта в `~/.hermes/.env` нужен хотя бы один ключ:

- `OPENROUTER_API_KEY` — дальше выбрать бесплатную модель с суффиксом `:free` через `hermes model`.
- `GEMINI_API_KEY` или `GOOGLE_API_KEY` — Google AI Studio / Gemini free tier.
- `HF_TOKEN` — Hugging Face Inference Providers, маленький бесплатный лимит.

Fallback-провайдеры настраиваются интерактивно:

```bash
hermes model
hermes fallback add
hermes fallback list
```

## Free router and Gemini fallback

Для текущего локального MVP можно использовать два режима:

Самый дешёвый OpenAI primary + бесплатный OpenRouter fallback:

```env
HERMES_MODE=openai_router
OPENAI_MODEL=gpt-5-nano
HERMES_CLI_MODELS=openrouter/free
AI_ALLOW_PAID_FALLBACK=false
```

Только бесплатный OpenRouter:

```env
HERMES_MODE=cli_router
HERMES_CLI_COMMAND_TEMPLATE=hermes --provider openrouter --model {model} --oneshot {prompt}
HERMES_CLI_MODELS=openrouter/free
HERMES_PAID_FALLBACK_MODELS=
AI_ALLOW_PAID_FALLBACK=false
```

`openrouter/free` — бесплатный router OpenRouter. Он выбирает бесплатную модель сам, но качество может плавать.

Gemini Flash Lite через OpenRouter сейчас не бесплатный: в OpenRouter `/models` у `google/gemini-2.5-flash-lite` ненулевая цена. Поэтому он не используется в free-only режиме. Включай его только явно:

```env
AI_ALLOW_PAID_FALLBACK=true
```

Fallback срабатывает в двух случаях:

- первая модель вернула provider error (`HTTP 400/401/429/5xx`);
- первая модель вернула низкокачественный ответ с признаками внутренних рассуждений или raw error.

### Provider policy

`provider_transport` отвечает только за HTTP/CLI вызов. `provider_policy` выбирает кандидата до вызова transport: проверяет capability, JSON support, cooldown, rolling quality score, известную latency, оценочную цену и общий deadline.

```env
# Только FREE/LOCAL providers. CHEAP и PAID даже не получают transport call.
AI_COST_MODE=free_only

# FREE/LOCAL/CHEAP. Надёжный CHEAP/LOCAL идёт первым, FREE остаётся fallback.
# Для расходов нужен явный положительный бюджет.
# AI_COST_MODE=cheap

# Может использовать PAID только если он отдельно добавлен через AI_ALLOW_PAID_FALLBACK=true.
# AI_COST_MODE=balanced

AI_PROVIDER_DEADLINE_SECONDS=15
AI_PROVIDER_MAX_ATTEMPTS=2
AI_PROVIDER_COOLDOWN_SECONDS=120
AI_PROVIDER_DAILY_BUDGET_MICRO_USD=0
AI_PROVIDER_MIN_QUALITY_SCORE=60
```

Budget ledger хранит **оценочный** расход на попытку в USD micro-units (`1_000` = `$0.001`), а не выдаёт себя за точный billing API. При `0` cheap/paid provider не резервируется; free/local request разрешён. Не-free `OPENROUTER_MODEL` или CLI model автоматически получает класс `cheap`, поэтому не может случайно пройти в `free_only`.

Для стабильного production-профиля после benchmark сначала оставь только прошедший primary:

```env
AI_COST_MODE=cheap
AI_PROVIDER_DAILY_BUDGET_MICRO_USD=1000000
OPENROUTER_ENABLED=false
HERMES_CLI_ENABLED=false
```

Ключи при этом можно сохранить в `.env`: flags выключают transport до вызова. Возвращай free gateways только после нового `scripts/provider_benchmark.py --gate`, если они снова проходят quality и latency thresholds.

В `free_only` используй `HERMES_MODE=provider_router` или `cli_router`. Прямые `HERMES_MODE=cli` и `http` намеренно отклоняются: policy не может доказать стоимость непрозрачного внешнего маршрута.

Проверка adapter без Telegram:

```bash
scripts/local_check.sh
.venv/bin/python scripts/hermes_adapter_smoke.py
```

## Hermes

MVP-правило: dangerous tools выключены.

Разрешено:

- LLM reasoning;
- provider fallback;
- безопасный memory context.

Запрещено:

- shell;
- чтение/запись файлов сервера;
- Docker socket;
- SSH;
- произвольный browser automation;
- доступ к секретам.

## Free-first режим

По умолчанию:

```env
AI_COST_MODE=free_only
AI_ALLOW_PAID_FALLBACK=false
```

Платный fallback не используется, пока ты явно не включишь его в env.

`free_only` разрешает только provider с явным классом `free` или `local`; он не зависит от порядка fallback и не вызывает OpenAI/paid CLI model. Для `cheap` и `balanced` задай положительный `AI_PROVIDER_DAILY_BUDGET_MICRO_USD`, иначе policy безопасно оставит только бесплатные кандидаты.

## Миграции базы

Alembic — единственный владелец схемы. ORM не вызывает `create_all`, сервисы не делают legacy `ALTER`, а неизвестная база без `alembic_version` не получает автоматический `stamp`.

Перед запуском сервисов применяй миграции отдельным шагом:

```bash
scripts/migrate.sh
```

`scripts/preflight.py` применяет Alembic migration и затем проверяет, что revision совпадает с head. Для старой непомеченной базы сначала сделай backup и установи её revision вручную после отдельной проверки. Автоматического `stamp head` нет.

В Docker Compose backend и bot стартуют через:

```bash
scripts/docker_start_backend.sh
scripts/docker_start_bot.sh
```

Compose запускает отдельный одноразовый `migrate` service. Backend, bot и collector стартуют только после его успешного завершения. `init_db` сохранён как compatibility helper для локальных тестов, но вызывает Alembic, а не ORM DDL.

Для production PostgreSQL используй отдельный override:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

Задай `POSTGRES_PASSWORD` и PostgreSQL `DATABASE_URL` через `.env`. SQLite остаётся dev adapter с WAL и `busy_timeout`; при `APP_ENV=production` preflight требует PostgreSQL URL.

## Golden eval для natural UX

Golden-набор лежит в:

```text
tests/golden_dialogs/natural_ux.json
```

Запуск:

```bash
.venv/bin/python scripts/eval_golden.py
```

Скрипт проверяет 30+ русских фраз:

- какие actions извлёк deterministic router;
- не исполнил ли он случайное действие на непонятной фразе;
- проходят ли базовые ответы quality gate;
- не стали ли ответы слишком длинными.

JSON-отчёт пишется в:

```text
reports/golden_eval/
```

## Provider quality benchmark

Обычный отчёт без падения как gate:

```bash
.venv/bin/python scripts/provider_benchmark.py
```

Можно ограничить конкретным provider:

```bash
.venv/bin/python scripts/provider_benchmark.py --provider openrouter_free
```

Регулярный gate перед релизом или по cron:

```bash
scripts/provider_quality_gate.sh
```

По умолчанию gate проверяет всех включённых провайдеров из `.env` в двух профилях:

1. direct API: OpenRouter/free, OpenAI cheap, опциональные Groq/HF;
2. local/CLI: Hermes CLI.

Пороговые значения для direct API:

- `fail_rate <= 20%`;
- `quality_score >= 75`;
- `avg_latency_ms <= 12000`;
- `p95_latency_ms <= 20000`.

Regular gate считается пройденным, если прошёл хотя бы один direct provider. Это защищает реальный router path: если OpenRouter/free просел по лимитам, но OpenAI cheap живой, сервис всё ещё отвечает. Чтобы требовать больше провайдеров:

```bash
PROVIDER_GATE_DIRECT_MIN_PASSING=2 scripts/provider_quality_gate.sh
```

Hermes CLI проверяется отдельно, потому что он может включать локальный запуск, CLI overhead и чужой router за ним. В regular gate он измеряется и пишет отчёт, но не блокирует релиз по умолчанию. Чтобы сделать Hermes CLI обязательным:

```bash
PROVIDER_GATE_REQUIRE_LOCAL=1 scripts/provider_quality_gate.sh
```

Пороги для local/CLI:

- `fail_rate <= 30%`;
- `quality_score >= 70`;
- `avg_latency_ms <= 15000`;
- `p95_latency_ms <= 45000`.

Если нужен быстрый gate только по direct API:

```bash
PROVIDER_GATE_RUN_LOCAL=0 scripts/provider_quality_gate.sh
```

Пороги direct API можно менять env-переменными:

```bash
PROVIDER_GATE_MAX_FAIL_RATE=0.30 \
PROVIDER_GATE_MIN_QUALITY_SCORE=70 \
PROVIDER_GATE_MAX_AVG_LATENCY_MS=15000 \
PROVIDER_GATE_MAX_P95_LATENCY_MS=25000 \
PROVIDER_GATE_DIRECT_MIN_PASSING=1 \
scripts/provider_quality_gate.sh --provider openrouter_free
```

Пороги local/CLI:

```bash
PROVIDER_GATE_LOCAL_MAX_FAIL_RATE=0.30 \
PROVIDER_GATE_LOCAL_MIN_QUALITY_SCORE=70 \
PROVIDER_GATE_LOCAL_MAX_AVG_LATENCY_MS=15000 \
PROVIDER_GATE_LOCAL_MAX_P95_LATENCY_MS=45000 \
scripts/provider_quality_gate.sh
```

Включить этот gate в общий локальный чек:

```bash
RUN_PROVIDER_BENCHMARK_GATE=1 scripts/local_check.sh
```

Отчёт пишется в:

```text
reports/provider_benchmarks/
```

В отчёте есть:

- provider/model;
- latency: `avg_latency_ms`, `p95_latency_ms`, `max_latency_ms`;
- `fail_rate` и `success_rate`;
- `quality_score` 0–100;
- gate result и причины провала;
- короткий preview ответа без секретов.

Benchmark делает реальные LLM-запросы к провайдерам из `.env`, поэтому запускай его осознанно.

## Стиль общения

Обычные AI-ответы получают отдельный system prompt, собранный по измеримым признакам локального redacted-корпуса Telegram. Raw-посты в репозиторий и runtime prompt не входят. JSON extractor, actions и tools стилевой overlay не получают.

```env
AI_STYLE_ENABLED=true
AI_STYLE_PROMPT_PATH=
```

Проверка:

```bash
.venv/bin/python scripts/eval_style.py
```

Подробно про данные, transports, ограничения fine-tune и A/B-проверку: [docs/communication-style.md](docs/communication-style.md).

### Закрытый model holdout

Для реального сравнения base, runtime style profile и fine-tuned кандидата используй отдельный
локальный holdout из 100 запросов. Он gitignored и не должен попадать ни в один training-файл:

```bash
.venv/bin/python scripts/generate_model_holdout.py
.venv/bin/python scripts/eval_model_holdout.py --gate
```

Base всегда `gpt-5-nano`. Style profile использует ту же модель и настоящий runtime budget/
normalizer. Fine-tuned кандидат запускается только если задан `FINE_TUNED_MODEL`; без него в
отчёте будет `skipped_not_configured`.

Победа требует одновременно: качество не ниже `90/100`, короткие ответы не ниже `90%`,
лишние вопросы не выше `10%`, ноль зафиксированных factual violations, ноль регрессий
router/safety и p50 latency не хуже базы более чем на `20%`. Отчёты остаются локально в
`reports/model_holdout/`.

### Согласованные примеры для обучения

По умолчанию кнопки обучения выключены, чтобы не шуметь в обычном чате. Чтобы включить сбор
согласованных примеров, задай `TRAINING_FEEDBACK_BUTTONS_ENABLED=true`.

Когда сбор включён, под обычным AI-ответом бот показывает три кнопки: `Нормально`,
`Сделай короче` и `Я исправил сам`.
Нажатие `Нормально` — явное согласие сохранить пару «твой запрос → ответ». `Сделай короче`
создаёт новый кандидат, который тоже нужно отдельно одобрить. После `Я исправил сам` пришли
следующей репликой исправленную версию: в набор попадёт именно она, а не исходный ответ.

Перед записью автоматически маскируются ключи, телефоны, email, URL, Telegram handles, IP,
локальные пути и явно названное имя. В `data/` (gitignored) попадают только эти очищенные
пары; обычная история Telegram не является training dataset.

Набор намеренно разделён по назначению, чтобы стиль не ломал техническое поведение:

- `short_answer`: 120 коротких ответов на обычные вопросы;
- `plan_decision`: 50 планов и решений;
- `message_draft`: 40 готовых сообщений людям;
- `insufficient_data`: 30 честных ответов при нехватке данных;
- `clarification`: 30 уточняющих вопросов;
- `safe_refusal`: 30 безопасных отказов;
- `preference_pairs`: 80–120 явных пар «плохой ответ → исправленный хороший ответ»;
  рекомендуемая точка — 100.

Это 300 SFT-примеров и 80–120 отдельных preference-пар. Каждая preference-пара хранит
`prompt`, длинный/гладкий `rejected`, короткий/конкретный `chosen` и локально выведенное
`why_chosen`: почему новая формулировка лучше. `JSON extractor`, tool calls,
напоминания, задачи и Calendar не получают эти кнопки и не экспортируются в набор.
Отредактированные ответы уходят только в `preference_pairs.jsonl`, а не смешиваются
автоматически с SFT-файлами.

Когда накопятся 300 SFT-примеров и минимум 80 preference-пар, собери локальные файлы:

```bash
.venv/bin/python scripts/export_training_feedback.py \
  --tg-user-id "$TG_USER_ID" \
  --confirm-consent \
  --output-dir data/training/feedback \
  --require-targets
```

Экспорт создаёт шесть JSONL по категориям, отдельный `preference_pairs.jsonl` и
`manifest.json` с количеством, целями и пробелами. Без `--require-targets` можно
выгрузить промежуточный набор для локального review; команда не выдаст его за готовый
к обучению.

Перед любым внешним fine-tune вручную проверь файл: автоматическая очистка снижает риск,
но не может безошибочно распознать весь личный контекст.

## Automation runtime

Все фоновые процессы используют один lifecycle из `assistant/automation_runtime.py`:

- `actions`;
- `delivery_outbox`;
- `reminders`;
- `monitors`;
- `telegram_trends`.

`AutomationRuntime` атомарно берёт lease на тип worker через таблицу
`automation_worker_leases`. Пока lease активен, второй bot/cron/process не входит во внутренний
item-claim этого adapter. Runtime отвечает за heartbeat, timeout, retry budget, exponential backoff,
lifecycle logs и takeover после истечения lease. Конкретный adapter отвечает только за один
ограниченный `run_once()` и сохранение своего бизнес-результата.

Actions и delivery дополнительно имеют item-level fencing: `worker_id`, `claimed_at`, `heartbeat_at`
и `lease_until`. Claim выполняется атомарным CAS update. Долгий tool/send call продлевает heartbeat;
после истечения lease запись возвращается в очередь. Старый worker после takeover не может записать
`succeeded/sent`, поэтому два процесса не завершают одну запись одновременно.

При первом запуске после миграции и после stale takeover adapters восстанавливают зависшие записи:

- `agent_actions.running` → `queued`;
- `delivery_outbox.sending` → `queued`;
- `reminders.sending` → `pending` или `failed`, если budget уже исчерпан.

Bot запускает один runtime для action/outbox/reminder. Monitor cron и trend process создают тот же
runtime со своими adapters и используют общую SQL lease-таблицу. Перед запуском обязательно применить
миграции:

```bash
.venv/bin/python scripts/run_migrations.py
```

Модель доставки остаётся **at-least-once**. Lease и idempotency защищают от параллельного claim и
обычных рестартов, но внешний Trello/Calendar API теоретически может получить повтор, если процесс
умер строго после внешнего side effect и до записи `succeeded`. Для exactly-once внешние providers
должны поддерживать собственный idempotency key.

## Proactive monitors

Proactive monitor проверяет внешний источник и пишет в Telegram только при выполнении условия. Управление идёт через команды бота, а проверка запускается отдельным cron/systemd runner.

Поддерживаемые источники:

- `github_releases` — читает latest release через публичный `api.github.com`.
- `rss` — читает RSS/Atom-like XML по HTTPS и сравнивает последние items.
- `http_api` — читает JSON endpoint только по HTTPS и только если host явно указан в `allowed_hosts`.
- `telegram_trends` — использует локальную базу Telegram collector/trendwatch как monitor source.

### Команды

Добавить monitor:

```text
/monitor add github_releases openai/codex | condition=напиши мне только если вышел важный релиз
/monitor add rss https://example.com/feed.xml | condition=напиши если появилась важная статья
/monitor add http_api https://api.example.com/status | allowed_hosts=api.example.com | condition=напиши если статус стал critical
/monitor add telegram_trends | condition=напиши если в чатах появилась новая повторяющаяся тема
```

Показать свои monitors:

```text
/monitor list
```

Выключить monitor без физического удаления строки:

```text
/monitor remove 1
```

`/monitor remove` работает только для monitor текущего пользователя. Чужой monitor не выключается.

### Как работает runner

Конфиг источника хранится в `monitor_jobs.source_config`:

```json
{"owner":"openai","repo":"codex","quiet_hours":"23:00-08:00"}
```

Runner сравнивает hash нового payload с `last_state_hash`. Если hash не изменился, он пишет `monitor_runs.status=no_change` и молчит. Если payload изменился, runner проверяет дневной LLM budget, отдаёт предыдущее и новое состояние в текущий Hermes/provider router и ждёт строгий JSON:

```json
{"triggered": true, "message": "Короткое сообщение для Telegram"}
```

Если `triggered=false`, Telegram-сообщение не создаётся. Если `triggered=true`, сообщение попадает в existing delivery outbox и доставляется тем же worker'ом, что и остальные отложенные ответы. Delivery получает idempotency key `monitor:<id>:<state_hash>`, поэтому повторный runner не создаёт дубль. Если monitor попал в `quiet_hours`, событие записывается как `deferred_quiet_hours`, а доставляется позже через Daily Brief. Секреты для GitHub releases/RSS не нужны.

Запуск одного прохода:

```bash
.venv/bin/python scripts/run_monitors_once.py --limit 50
.venv/bin/python scripts/run_monitors_once.py --limit 50 --daily-llm-budget 25 --daily-brief
```

Для cron можно запускать раз в 10–30 минут:

```cron
*/15 * * * * cd /opt/jarhert && .venv/bin/python scripts/run_monitors_once.py --limit 50 --daily-llm-budget 25 >> /var/log/jarhert-monitors.log 2>&1
5 9 * * * cd /opt/jarhert && .venv/bin/python scripts/run_monitors_once.py --limit 0 --daily-brief >> /var/log/jarhert-monitor-brief.log 2>&1
```

Пример systemd unit:

```ini
[Unit]
Description=JarHert proactive monitors

[Service]
WorkingDirectory=/opt/jarhert
EnvironmentFile=/opt/jarhert/.env
ExecStart=/opt/jarhert/.venv/bin/python scripts/run_monitors_once.py --limit 50
Type=oneshot
```

Пример timer:

```ini
[Unit]
Description=Run JarHert proactive monitors every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Unit=jarhert-monitors.service

[Install]
WantedBy=timers.target
```

Частоту хранит внешний scheduler, не сама таблица `monitor_jobs`.

### Planner DAG для длинных задач

Длинные планы строятся как DAG поверх существующих `agent_jobs` и `agent_actions`.
Каждый node — allowlisted action из Tool Registry. Planner не даёт Hermes прямой shell/file/server доступ:
он только раскладывает план на разрешённые tools.

Поддержано:

- `dependencies` через совместимое `depends_on_action_id` и полный список родителей для join-node;
- checkpoints из `succeeded` actions с `result_meta/result_text`;
- `pause/resume/cancel` на уровне job;
- partial results из уже завершённых шагов;
- compensation candidates по external ids (`trello_card_id`, `calendar_event_id`, `*_url`) через существующий `result_meta`.

## Telegram chat collector

Collector — отдельный лёгкий процесс без LLM. Он постоянно слушает выбранные Telegram-чаты/каналы через MTProto user-client и пишет сырые сообщения в SQLite-таблицу `messages`. Агент потом отдельным cron worker'ом читает необработанные сообщения, делает trendwatch summary через текущий provider router и кладёт итог в delivery outbox.

Важно: collector не смешан с `gateway_bot`, не использует `BOT_TOKEN` и не делает AI-обработку.

### Получить Telegram API ID/hash

1. Открой [my.telegram.org/apps](https://my.telegram.org/apps).
2. Войди под своим Telegram-аккаунтом.
3. Создай приложение и скопируй `api_id` / `api_hash`.
4. Не коммить эти значения и session-файл.

### Настройки

В `.env`:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...
TELEGRAM_COLLECTOR_SESSION=/opt/jarhert/data/telegram_collector.session
TELEGRAM_COLLECTOR_CHATS=@channel_one,-1001234567890
TELEGRAM_COLLECTOR_HEALTH_HOST=0.0.0.0
TELEGRAM_COLLECTOR_HEALTH_PORT=8091

TELEGRAM_TREND_TG_USER_ID=<your_tg_user_id>
TELEGRAM_TREND_INTERVAL_SECONDS=3600
TELEGRAM_TREND_LOOKBACK_HOURS=6
TELEGRAM_TREND_BATCH_LIMIT=300
```

Если чатов много, лучше отдельный JSON:

```json
{"chats": ["@channel_one", "-1001234567890"]}
```

И в `.env`:

```env
TELEGRAM_COLLECTOR_CHATS_FILE=/opt/jarhert/config/collector_chats.json
```

### Первый запуск session

Локально:

```bash
.venv/bin/python -m telegram_collector.app
```

Telethon попросит телефон, код и, если включено, 2FA-пароль. После этого появится session-файл из `TELEGRAM_COLLECTOR_SESSION`. На VPS лучше положить session в `/opt/jarhert/data/telegram_collector.session` и выдать права только пользователю сервиса:

```bash
chmod 600 /opt/jarhert/data/telegram_collector.session
```

### VPS: отдельные процессы

Collector:

```bash
cd /opt/jarhert
set -a
. ./.env
set +a
.venv/bin/python -m telegram_collector.app
```

Trend worker:

```bash
cd /opt/jarhert
set -a
. ./.env
set +a
.venv/bin/python scripts/run_telegram_trend_worker.py
```

Один tick для проверки:

```bash
.venv/bin/python scripts/run_telegram_trend_worker.py --once
```

Health collector:

```bash
curl http://127.0.0.1:8091/health
```

Docker Compose services вынесены в profile `collector`, чтобы не ломать основной bot/backend:

```bash
docker compose --profile collector up -d telegram_collector trend_worker
```

## Production smoke

Один smoke-скрипт:

```bash
BASE_URL=https://your-domain.example \
ASSISTANT_SERVICE_TOKEN=... \
TG_USER_ID=<admin_tg_user_id> \
scripts/production_smoke.sh
```

Он проверяет:

- `/health`;
- `/api/version`;
- что `/api/assistant/telegram-text` без токена даёт `401`;
- `/admin_status` через защищённый endpoint;
- provider response metadata;
- Trello/Calendar health внутри admin status.

Telegram delivery специально выключен по умолчанию. Чтобы проверить полный live delivery:

```bash
BASE_URL=https://your-domain.example \
ASSISTANT_SERVICE_TOKEN=... \
TG_USER_ID=<admin_tg_user_id> \
SEND_TELEGRAM=1 \
scripts/production_smoke.sh
```

Это отправит реальные сообщения в Telegram и создаст тестовые task/calendar-действия через `scripts/live_e2e.py`.

## Release 9.5 gate

Единый release gate запускается только из чистого Git working tree:

```bash
RELEASE_GATE_TG_USER_ID=<your_tg_user_id> \
RELEASE_GATE_VOICE_FILE=dev/voice_fixtures/live.oga \
RELEASE_GATE_BOT_USERNAME=<bot_username> \
scripts/release_95_gate.sh
```

В одном запуске проверяются clean clone, Alembic lifecycle, весь pytest, golden dialogs, живой provider benchmark, поиск секретов в HEAD и всей Git history, конкурентные claims, bounded load, kill-worker recovery, backup/restore canary и полный Telegram text/voice → provider → approval → Trello/Calendar/reminder → outbox путь.

Отчёт сохраняется в `reports/release_95/<timestamp>/scorecard.json`. Оценка `9.5` возможна только когда все обязательные gates имеют статус `passed` в одном отчёте. Ошибка, отсутствующая конфигурация или `skipped` дают ненулевой exit code.

Для локальной диагностики без сообщений и side effects в Telegram можно явно выполнить:

```bash
RELEASE_GATE_SKIP_LIVE=1 scripts/release_95_gate.sh
```

Этот режим намеренно остаётся красным: scorecard отмечает live Telegram как `skipped` и не выдаёт 9.5. Provider benchmark также не допускает пустой набор и по умолчанию требует прохождения всех включённых providers. Временный порог `PROVIDER_GATE_MIN_PASSING_PROVIDERS` можно задать явно, но для финального 9.5 release его использовать нельзя.

## Документация для первых пользователей

Короткий старт для друзей лежит в:

```text
docs/first-users.md
```

Там описано, что писать боту, какие действия требуют подтверждения, какие есть ограничения и как проверить voice e2e.
