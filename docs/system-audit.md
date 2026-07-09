# AI Brooch system audit

## Current state

AI Brooch уже умеет:

- отвечать в Telegram через OpenAI primary и OpenRouter/Hermes fallback;
- сохранять важное через `/remember`;
- сохранять идеи через `/idea`;
- ставить и присылать напоминания через `/remind`;
- принимать голосовые, транскрибировать и выполнять как текст;
- хранить всё локально в SQLite;
- ограничивать ежедневные AI-запросы;
- блокировать опасные запросы к shell/files/secrets/server.

## Local reusable code found

Найден старый проект:

```text
/Users/mihailkulibaba/Downloads/old/interview-reviewer
```

Useful parts:

- `review_sheets.py` — service account → Google Sheets sync, worksheet creation, headers, upsert, graceful disable.
- `transcriber.py` — multiple transcription backends and formatting ideas.
- `converter.py` — ffmpeg-based audio conversion pattern.
- `job_queue.py` — durable queued background processing pattern.
- `google_drive.py` — robust public Google Drive file download, not needed for first assistant MVP.

Not copied directly:

- interview-specific `JobRecord`;
- human review queue;
- Telegram bot framework code;
- old secrets/session files.

## Already reused

- Google Sheets service-account sync pattern adapted into `assistant/google_sheets_sync.py`.
- Audio conversion fallback pattern adapted into `assistant/transcription.py`.
- Graceful disable pattern: if Google sync dependencies/env are missing, assistant still works locally.
- Task Command Center Trello/Google Calendar CLI adapted as a local tool through `assistant/task_command_center.py`.

## Task Command Center status

Found:

```text
TASK_COMMAND_CENTER_DIR=/opt/task-command-center
```

It contains:

- `src/trello_client.py`;
- `src/google_calendar_client.py`;
- `taskctl.py`;
- Task Command Center `.env` with Trello credentials;
- Google OAuth `client_secret.json`;
- Google OAuth `token.json`.

Verification:

- Trello real API: `list --list Today` works.
- Google Calendar: current `token.json` exists and has calendar scope, but the refresh is invalid/expired. Re-run OAuth before using Calendar writes.

AI Brooch now exposes:

- `/task`;
- `/tasks`;
- `/task_move`;
- `/task_done`;
- `/calendar`.

## Highest-value optimizations

1. Durable action queue
   - Move voice transcription, Google sync, and future long actions into a DB-backed queue.
   - Prevents Telegram timeout and makes retries safe.

2. Google Sheets as operational journal
   - Ideas/reminders go into a structured sheet.
   - Useful immediately and simpler than Google Docs API.

3. Better command extraction from voice
   - Add a small deterministic parser for:
     - "запиши идею...";
     - "напомни завтра...";
     - "запомни...";
     - "список напоминаний".

4. Reminder parser upgrade
   - Support Russian natural dates:
     - "завтра утром";
     - "в пятницу";
     - "через полчаса";
     - "сегодня вечером".

5. Personal daily digest
   - `/daily` returns today's reminders and last ideas.
   - Optional daily scheduled message.

6. Import/export
   - `/export` sends ideas/reminders as Markdown.
   - Keeps data portable.

7. Safety dashboard command
   - `/admin_status` should include provider health, queue size, Google sync status, last error.

## Recommended next implementation order

1. Finish Google Sheets setup and verify one real write.
2. Add `/daily` and `/export`.
3. Add DB-backed action queue for transcription/sync retries.
4. Improve Russian reminder parsing.
5. Add provider health status and fallback diagnostics.
