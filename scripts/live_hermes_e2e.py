from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass
class Step:
    name: str
    ok: bool
    latency_ms: int
    telegram_message_id: int | None = None
    detail: str = ""


def load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def bot_username(token: str) -> str:
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(1_000_000))
    username = str((payload.get("result") or {}).get("username") or "")
    if not payload.get("ok") or not username:
        raise RuntimeError("Telegram getMe failed")
    return username


async def wait_message(client, entity, *, after_id: int, predicate: Callable[[Any], bool], timeout: int):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async for message in client.iter_messages(entity, limit=20):
            if int(message.id) > after_id and not message.out and predicate(message):
                return message
        await asyncio.sleep(1)
    raise TimeoutError("Telegram response timeout")


def buttons(message) -> list[str]:
    return [str(button.text) for row in (message.buttons or []) for button in row]


def has_bad_reply(message) -> bool:
    text = str(message.message or "").lower()
    return any(marker in text for marker in ("лимит", "уточни действие", "not configured", "не настроен"))


async def send_plain(client, entity, text: str, timeout: int) -> tuple[Any, int]:
    started = time.perf_counter()
    sent = await client.send_message(entity, text)
    reply = await wait_message(
        client,
        entity,
        after_id=int(sent.id),
        predicate=lambda message: bool(str(message.message or "").strip()),
        timeout=timeout,
    )
    if has_bad_reply(reply):
        raise RuntimeError("Bot returned blocked or clarification reply")
    return reply, int((time.perf_counter() - started) * 1000)


async def send_confirmed(
    client,
    entity,
    text: str,
    timeout: int,
    *,
    approval_text: str = "Выполнить",
) -> tuple[Any, int]:
    started = time.perf_counter()
    sent = await client.send_message(entity, text)
    approval = await wait_message(
        client,
        entity,
        after_id=int(sent.id),
        predicate=lambda message: approval_text in buttons(message),
        timeout=timeout,
    )
    await approval.click(text=approval_text)
    result = await wait_message(
        client,
        entity,
        after_id=int(approval.id) - 1,
        predicate=lambda message: approval_text not in buttons(message)
        and (bool(str(message.message or "").strip()) or message.file is not None),
        timeout=timeout,
    )
    if has_bad_reply(result):
        raise RuntimeError("Confirmed action returned blocked reply")
    return result, int((time.perf_counter() - started) * 1000)


async def run(args, steps: list[Step]) -> None:
    try:
        from telethon import TelegramClient
    except ModuleNotFoundError as error:
        raise RuntimeError("Install Telethon before live E2E") from error
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    username = bot_username(token)
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session = os.environ["TELEGRAM_USER_SESSION"]
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("MTProto session is not authorized")
    entity = await client.get_entity(f"@{username}")
    run_id = uuid.uuid4().hex[:8]
    task_title = f"JarHert E2E {run_id}"
    event_title = f"JarHert Calendar E2E {run_id}"
    try:
        reply, latency = await send_plain(client, entity, "Ответь одной короткой фразой: ты на связи?", args.timeout)
        steps.append(Step("llm_reply", True, latency, int(reply.id)))

        reply, latency = await send_confirmed(
            client, entity, f"Создай в Trello задачу «{task_title}» в списке Today", args.timeout
        )
        steps.append(Step("trello_create", True, latency, int(reply.id)))
        reply, latency = await send_confirmed(
            client, entity, f"Удали из Trello задачу «{task_title}»", args.timeout
        )
        steps.append(Step("trello_delete", True, latency, int(reply.id)))

        reply, latency = await send_confirmed(
            client,
            entity,
            f"Создай в Google Calendar событие «{event_title}» на 2030-01-02 с 12:00 до 12:15 по Москве",
            args.timeout,
        )
        steps.append(Step("calendar_create", True, latency, int(reply.id)))
        reply, latency = await send_confirmed(
            client, entity, f"Удали из Google Calendar событие «{event_title}»", args.timeout
        )
        steps.append(Step("calendar_delete", True, latency, int(reply.id)))

        reply, latency = await send_confirmed(
            client,
            entity,
            f"Экспортируй текст из чата @{username} в TXT, максимум 20 сообщений",
            args.timeout,
            approval_text="Экспортировать",
        )
        filename = str(getattr(reply.file, "name", "") or "") if reply.file else ""
        if not filename.lower().endswith(".txt"):
            raise RuntimeError("Telegram export did not return TXT document")
        steps.append(Step("chat_export", True, latency, int(reply.id), detail="txt_document"))
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-home", type=Path, default=Path.home() / ".hermes" / "profiles" / "jarhert")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--report", type=Path, default=Path("reports/live_hermes_e2e.json"))
    args = parser.parse_args()
    load_env(args.profile_home / ".env")
    started_at = datetime.now(timezone.utc).isoformat()
    steps: list[Step] = []
    try:
        asyncio.run(run(args, steps))
        ok = all(step.ok for step in steps) and len(steps) == 6
        error = None
    except Exception as exc:
        ok = False
        error = f"{type(exc).__name__}: {exc}"
    payload = {"ok": ok, "started_at": started_at, "steps": [asdict(step) for step in steps], "error": error}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "steps": len(steps), "report": str(args.report)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
