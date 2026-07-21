# JarHert: рабочий протокол для AI-агента

Этот файл нужен любому агенту, который продолжает разработку JarHert. Он задаёт
границы проекта, порядок работы, проверки и безопасный деплой. Его можно передать
Codex, Qwen или другому coding agent вместе с конкретной задачей.

## Готовый стартовый промпт

```text
Ты работаешь с JarHert как ведущий инженер и доводишь задачу до проверенного
результата. Не ограничивайся советом, если я попросил исправить, запушить или
задеплоить.

Локальный проект:
/Users/mihailkulibaba/Documents/telegram-ai-brooch

Личный production VDS JarHert:
deploy@89.124.124.212
профиль: /home/deploy/.hermes/profiles/jarhert

Сервер 89.124.84.4 рабочий и не относится к JarHert. Никогда не копируй туда
JarHert, не перезапускай там сервисы и не меняй его конфигурацию.

Перед работой:
1. Прочитай AGENTS.md, hermes/AGENTS.md, README.md и docs/agent-workflow.md.
2. Проверь git status, текущую ветку, HEAD и origin/main.
3. Не потеряй и не включай в коммит чужие незакоммиченные изменения.
4. Если основной checkout грязный, создай отдельный clean worktree от актуального
   origin/main или продолжи уже подготовленный worktree только после проверки его
   истории и diff.
5. Покажи короткий план до редактирования.

Во время работы:
- делай минимальный законченный diff;
- сохраняй текущую архитектуру Hermes profile и Personal OS;
- не читай и не печатай секреты;
- не ослабляй CSP, approvals, idempotency и personal-VPS guard;
- не запускай платные или live E2E без моего прямого запроса;
- для UI проверяй мобильный Telegram WebView, touch targets, safe areas, длинный
  русский текст, loading/error/empty states и отсутствие двойных обработчиков;
- ответы бота должны быть по-русски, коротко, без дублей, технического мусора и
  постоянного «Принял, обрабатываю»;
- один пользовательский план должен иметь одно понятное preview и одно
  подтверждение, если подтверждение требуется политикой.

Проверка:
1. Сначала запусти точечные тесты изменённого поведения.
2. Перед commit/push запусти scripts/local_check.sh.
3. Проверь итоговый diff на секреты, debug-код и посторонние файлы.
4. Сделай небольшой осмысленный коммит.
5. Push разрешён, когда я явно попросил push или deploy. Не делай force-push.
6. Перед деплоем local HEAD обязан совпадать с origin/main.
7. Деплой выполняй только через deploy/vps/sync_hermes_profile.sh. Скрипт должен
   подтвердить IP, hostname, SSH fingerprint и server-role marker.
8. После деплоя проверь gateway, dashboard, HTTPS, revision-файл и отсутствие
   второго Telegram gateway.

Если SSH просит passphrase, не проси прислать её в чат. Скажи выполнить локально
ssh-add ~/.ssh/id_rsa, затем продолжай сам.

В финале сообщи:
- что изменено;
- какие тесты реально запущены и их результат;
- local HEAD, origin/main и deployed hash;
- что проверено вживую, а что осталось непроверенным;
- точный безопасный следующий шаг, если остался блокер.
```

## Карта проекта

```text
Telegram
  -> Hermes gateway
  -> маршрутизация и policy
  -> JarHert native MCP tools
  -> Personal OS SQLite
  -> Trello / Google Calendar / GitHub / monitors / outbox

Mac coding runner
  -> отдельный sandbox/worktree
  -> diff и тесты
  -> отчёт в Telegram
  -> push/deploy только по явному запросу владельца
```

Основные части:

- `hermes/SOUL.md`: характер, язык и правила общения.
- `hermes/AGENTS.md`: ограничения runtime и инструментов.
- `hermes/config.yaml`: профиль Hermes и MCP tools.
- `hermes/native_tools/`: Personal OS, dashboard, очереди и интеграции.
- `hermes/skills/`: пользовательские сценарии.
- `deploy/vps/`: установка, синхронизация и проверка личного VDS.
- `tests/`: unit, contract и локальные integration tests.

## Неизменяемые границы

| Объект | Значение |
|---|---|
| Канонический локальный репозиторий | `/Users/mihailkulibaba/Documents/telegram-ai-brooch` |
| Личный VDS | `deploy@89.124.124.212` |
| Hostname личного VDS | `jarhert` |
| Серверный профиль | `/home/deploy/.hermes/profiles/jarhert` |
| Серверный checkout | `/home/deploy/jarhert-profile` |
| Рабочий сервер, запрещённый для JarHert | `89.124.84.4` |

Секреты живут только в приватном `.env` с правами `600` или в системном
хранилище учётных данных. Токены, пароли, OAuth credentials, SSH-ключи и session
files нельзя переносить в Git, тестовые fixtures, логи и ответы пользователю.

## Порядок работы

### 1. Осмотр

```bash
cd "/Users/mihailkulibaba/Documents/telegram-ai-brooch"
git status --short
git branch --show-current
git rev-parse HEAD
git fetch origin main
git rev-parse origin/main
```

Если checkout содержит чужие изменения, не очищай и не stash их автоматически.
Используй отдельный worktree:

```bash
JARHERT_WORKTREE="$(mktemp -d /tmp/jarhert-worktree.XXXXXX)"
git worktree add -b codex/jarhert-task "$JARHERT_WORKTREE" origin/main
```

### 2. Реализация

До изменения кода найди существующий путь выполнения и его тесты. Новую
абстракцию добавляй только тогда, когда она убирает реальное дублирование.
Изменения dashboard не должны пересоздавать лишние обработчики, ломать Telegram
safe areas или скрывать ошибку интеграции за зелёным статусом.

### 3. Недорогая проверка

Во время разработки запускай только затронутые тесты. Например:

```bash
.venv/bin/python -m pytest tests/test_hermes_dashboard.py -q
.venv/bin/python -m pytest tests/test_hermes_native_mcp.py -q
```

Не запускай live Telegram, реальные Trello/Calendar canary, платный provider
benchmark или дорогой STT без отдельной команды владельца.

### 4. Проверка перед публикацией

```bash
scripts/local_check.sh
git diff --check
git status --short
git diff --stat
```

Если окружению не хватает зависимости, сначала проверь, объявлена ли она в
requirements проекта. Не называй проверку зелёной, пока команда завершается с
ненулевым кодом.

### 5. Commit и push

```bash
git add <только-файлы-текущей-задачи>
git diff --cached --check
git commit -m "Короткое осмысленное описание"
git push origin HEAD:main
git ls-remote origin refs/heads/main
```

Перед прямым push в `main` после `git fetch origin main` проверь, что история
остаётся fast-forward. Если `origin/main` изменился, перенеси свои коммиты на
новую базу в отдельном worktree и повтори тесты. Force-push запрещён.

Если ключ заблокирован:

```bash
ssh-add ~/.ssh/id_rsa
```

Passphrase вводит владелец в локальном терминале. Агент не запрашивает и не
сохраняет её.

### 6. Деплой

Деплой разрешён только по прямому запросу владельца:

```bash
export JARHERT_VPS=deploy@89.124.124.212
deploy/vps/sync_hermes_profile.sh
```

Не обходи `deploy/vps/require_personal_vps.sh`. Sync принимает только чистый
worktree, где `HEAD == origin/main`, создаёт rollback-копию профиля и затем
перезапускает gateway и dashboard.

### 7. Проверка после деплоя

```bash
ssh deploy@89.124.124.212 \
  'systemctl --user is-active hermes-gateway-jarhert.service hermes-dashboard-jarhert.service'

ssh deploy@89.124.124.212 \
  'cat /home/deploy/.hermes/profiles/jarhert/state/jarhert-profile-revision.json'

JARHERT_VPS=deploy@89.124.124.212 \
  deploy/vps/verify_single_telegram_gateway.sh

curl -fsSI 'https://89.124.124.212.sslip.io/'
```

Для dashboard проверь HTTP 200, строгий CSP и `Cache-Control: no-store`. Живой
Telegram E2E выполняй только по явной просьбе. После canary удали созданные
тестовые карточки и события и перечисли cleanup в отчёте.

## Текущий незавершённый handoff

На момент создания файла подготовлен worktree:

```text
/Users/mihailkulibaba/Documents/telegram-ai-brooch/.qwen/worktrees/jarhert-port
```

Ветка `jarhert-cabinet-port` содержит два коммита поверх `6f3c485`:

- `d62235e`: owner autonomy и завершение preview execution;
- `e61e16b`: интерактивность кабинета и `completion_stats`.

Перед продолжением обязательно выполни `git fetch origin main` и заново проверь,
что эти коммиты ещё можно перенести fast-forward без потери новых изменений.
Старый отчёт о `365 passed` не заменяет свежий прогон. Ошибка из-за отсутствия
`pypdf` также должна быть устранена или честно зафиксирована до публикации.

## Критерий готовности

Работа завершена, когда выполнены все применимые пункты:

- запрос пользователя реально реализован;
- точечные тесты и `scripts/local_check.sh` завершились успешно;
- diff не содержит чужих правок, секретов и debug-кода;
- commit опубликован без force-push;
- local HEAD и `origin/main` совпадают;
- при запрошенном деплое deployed hash совпадает с ними;
- gateway и dashboard активны;
- UI проверен в Telegram или явно помечен как непроверенный вживую;
- временные внешние данные после canary удалены;
- финальный отчёт не скрывает пропущенные проверки.
