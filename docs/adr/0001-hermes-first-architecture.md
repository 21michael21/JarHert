# ADR 0001: Hermes-first Telegram AI assistant architecture

## Статус

Accepted for local MVP.

## Контекст

Нужен отдельный Telegram AI-помощник, который можно передать другому человеку и запустить независимо от Telegram Library. Обязательное условие: использовать Hermes Agent как AI-runtime. При этом MVP должен быть бесплатным или почти бесплатным, быстрым и безопасным.

Hermes уже даёт Telegram gateway, provider fallback, credential pools, memory, cron и tools. Но прямое подключение Telegram к Hermes даёт агенту слишком много власти и усложняет контроль качества.

## Решение

Делаем отдельный продукт `telegram-ai-brooch`.

Hermes используется как runtime за нашим gateway:

```text
Telegram -> Gateway -> Quality gates -> Hermes -> Quality gates -> Telegram
```

Gateway владеет:

- пользователями;
- лимитами;
- памятью MVP;
- напоминаниями;
- диагностикой;
- outbox/jobs в следующих этапах;
- запретом dangerous tools.

Hermes владеет:

- LLM reasoning;
- provider routing;
- fallback providers;
- будущими skills.

## Почему не чистый Hermes Gateway

Чистый Hermes Gateway быстрее включить, но сложнее контролировать:

- per-user лимиты;
- локальные фильтры качества;
- запрет shell/file/server actions;
- сохранение напоминаний в Postgres;
- переносимость состояния;
- диагностику без сырых пользовательских текстов.

## Quality gates

1. Input gate: пустой текст, длина, опасные действия.
2. Intent gate: команда, вопрос, заметка, напоминание.
3. Limit gate: daily user/global limit.
4. Hermes gate: timeout, bad response, fallback reason.
5. Output gate: пустой ответ, raw error, HTML, stack trace, слишком длинный ответ.
6. Delivery gate: Telegram send retry через outbox в следующем этапе.

## Безопасность

На MVP Hermes tools выключены. Если tools понадобятся позже, включаем только через allowlist и отдельный ADR.

Hermes должен запускаться под отдельным пользователем/контейнером без доступа к:

- `/root`;
- Docker socket;
- SSH keys;
- `.env` других сервисов;
- директории Telegram Library.

## Бесплатность

Free-first режим:

- Gemini;
- OpenRouter free models;
- Groq;
- Hugging Face.

Платный fallback выключен по умолчанию.

## Последствия

Плюсы:

- быстро получаем AI через Hermes;
- критичные функции остаются детерминированными;
- систему проще передать другому человеку;
- можно тестировать без реальных API.

Минусы:

- появляется gateway-слой;
- Hermes adapter надо поддерживать;
- free-tier качество всё равно зависит от внешних провайдеров.

