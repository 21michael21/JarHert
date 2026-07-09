# Telegram AI Brooch

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
```

## Trello и Google Calendar через Task Command Center

AI Brooch может использовать уже настроенный соседний проект:

```text
/Users/mihailkulibaba/Documents/New project/task-command-center
```

В нём лежат Trello `.env`, Google OAuth `client_secret.json` и `token.json`. AI Brooch не копирует эти секреты и не печатает их, а вызывает `taskctl.py` как локальный tool.

Настройки:

```env
TASK_COMMAND_CENTER_ENABLED=true
TASK_COMMAND_CENTER_DIR=/Users/mihailkulibaba/Documents/New project/task-command-center
TASK_COMMAND_CENTER_PYTHON=.venv/bin/python
TASK_COMMAND_CENTER_TIMEOUT_SECONDS=45
```

Проверка соседнего проекта:

```bash
cd "/Users/mihailkulibaba/Documents/New project/task-command-center"
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

Текущий статус:

- Trello real API работает: список `Today` читается.
- Google Calendar OAuth сейчас проходит health-check через `list_today_events()`.
- Если OAuth снова протухнет, обнови `token.json` так:

```bash
cd "/Users/mihailkulibaba/Documents/New project/task-command-center"
mv token.json token.json.bak
.venv/bin/python taskctl.py calendar-test
```

После browser OAuth команда `/calendar ...` в Telegram начнёт создавать события.

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

## Live e2e перед тестом с друзьями

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

## Миграции базы

Новый production-flow — Alembic:

```bash
scripts/migrate.sh
```

`scripts/preflight.py` тоже запускает миграции перед проверкой базы. Если база уже была создана старым `init_db`, migration runner безопасно ставит `alembic_version` через `stamp head` и не пересоздаёт таблицы.

В Docker Compose backend и bot стартуют через:

```bash
scripts/docker_start_backend.sh
scripts/docker_start_bot.sh
```

Оба скрипта сначала запускают миграции, потом поднимают сервис. Старый `init_db` остаётся для локальных тестов и обратной совместимости.

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

Запуск:

```bash
.venv/bin/python scripts/provider_benchmark.py
```

Можно ограничить конкретным provider:

```bash
.venv/bin/python scripts/provider_benchmark.py --provider openrouter_free
```

Отчёт пишется в:

```text
reports/provider_benchmarks/
```

В отчёте есть:

- provider/model;
- latency;
- quality gate result;
- fallback/error summary;
- короткий preview ответа без секретов.

Benchmark делает реальные LLM-запросы к провайдерам из `.env`, поэтому запускай его осознанно.

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

## Документация для первых пользователей

Короткий старт для друзей лежит в:

```text
docs/first-users.md
```

Там описано, что писать боту, какие действия требуют подтверждения, какие есть ограничения и как проверить voice e2e.
