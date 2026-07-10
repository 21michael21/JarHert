# Данные для настройки поведения

JarHert использует два разных источника, и их нельзя смешивать по смыслу:

- corpus постов задаёт наблюдаемый стиль и ритм;
- consented dialogs обучают отвечать на конкретный запрос пользователя.

Посты без роли `user` нельзя считать диалоговым fine-tuning набором. Сейчас они применяются через runtime system prompt, а не выдаются за доказательство понимания пользователя.

## Privacy audit

Проверка локального JSONL не печатает содержимое сообщений:

```bash
.venv/bin/python scripts/audit_training_data.py /local/private/dataset.jsonl
```

`--strict` возвращает ненулевой exit code при найденных email, телефонах, URL, Telegram handles, token-подобных строках или повреждённых записях. Автоматическая проверка не заменяет ручной просмотр личных имён и контекста.

Если audit нашёл технические данные, не переписывай private source. Сначала создай отдельную sanitized-копию в ignored `data/training/`, затем проверь уже её:

```bash
.venv/bin/python scripts/sanitize_training_data.py /local/private/dataset.jsonl
.venv/bin/python scripts/audit_training_data.py data/training/dataset.sanitized.jsonl --strict
```

Sanitizer сохраняет только `messages` и вычищает известные token/email/phone/URL/Telegram-handle, IPv4, credential assignment и локальные home paths. Исходные metadata намеренно не попадают в копию для обучения.

## Согласованные диалоги

Сначала выбери конкретные хорошие `conversation_turns.id`. Dry run ничего не записывает:

```bash
.venv/bin/python scripts/export_consented_dialogs.py \
  --tg-user-id 566055009 \
  --turn-id 12 --turn-id 18 \
  --dry-run
```

Экспорт требует явного подтверждения согласия и пишет только выбранные записи в ignored `data/`:

```bash
.venv/bin/python scripts/export_consented_dialogs.py \
  --tg-user-id 566055009 \
  --turn-id 12 --turn-id 18 \
  --confirm-consent
```

Перед внешним fine-tuning повторно запусти privacy audit и вручную проверь каждую пару. Не загружай raw Telegram export и автоматически собранную историю целиком.

## Что делать с длинными постами

Для коротких ответов не используй самостоятельные длинные публикации как SFT-примеры. Сначала локально
дистиллируй из них компактный runtime-профиль: каждая публикация получает максимум 500 символов веса,
поэтому длинный текст не заставляет ассистента отвечать длиннее.

Команды и критерии A/B описаны в [communication-style.md](communication-style.md). Это не fine-tune весов
модели: для него всё ещё нужны согласованные диалоги с реальными парами `user → assistant`.
