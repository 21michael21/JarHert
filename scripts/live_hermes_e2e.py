from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import tempfile
import time
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


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


def bot_identity(token: str) -> tuple[str, int]:
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(1_000_000))
    username = str((payload.get("result") or {}).get("username") or "")
    bot_id = int((payload.get("result") or {}).get("id") or 0)
    if not payload.get("ok") or not username or not bot_id:
        raise RuntimeError("Telegram getMe failed")
    return username, bot_id


def telethon_session_file(session: str) -> Path:
    """Return the SQLite file Telethon uses for a configured session name."""
    path = Path(session).expanduser()
    return path if path.suffix == ".session" else path.with_suffix(".session")


@contextmanager
def isolated_telethon_session(session: str) -> Iterator[str]:
    """Give a live check its own SQLite snapshot instead of locking the gateway session."""
    source = telethon_session_file(session)
    if not source.is_file():
        raise RuntimeError(f"Telethon session file is missing: {source}")
    with tempfile.TemporaryDirectory(prefix="jarhert-live-e2e-") as directory:
        destination = Path(directory) / source.name
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as reader, sqlite3.connect(destination) as writer:
            reader.backup(writer)
        yield str(destination)


async def wait_message(client, entity, *, after_id: int, predicate: Callable[[Any], bool], timeout: int):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async for message in client.iter_messages(entity, limit=20):
            if int(message.id) > after_id and not message.out and predicate(message):
                return message
        await asyncio.sleep(1)
    raise TimeoutError("Telegram response timeout")


async def wait_confirmation_result(client, entity, approval, approval_text: str, timeout: int):
    """Telegram callbacks may edit the approval message or send a new result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        updated = await client.get_messages(entity, ids=int(approval.id))
        if updated is not None and approval_button(updated, approval_text) is None:
            return updated
        async for message in client.iter_messages(entity, limit=20):
            if int(message.id) > int(approval.id) and not message.out and approval_button(message, approval_text) is None:
                return message
        await asyncio.sleep(1)
    raise TimeoutError("Telegram confirmation result timeout")


def buttons(message) -> list[str]:
    return [str(button.text) for row in (message.buttons or []) for button in row]


def approval_button(message, approval_text: str) -> str | None:
    labels = buttons(message)
    if approval_text in labels:
        return approval_text
    text = str(message.message or "").lower()
    if "1" in labels and approval_text.lower() in text:
        return "1"
    if approval_text.lower() in text:
        positive = ("approve", "allow", "confirm", "разреш", "подтверд", "выполн", "экспорт")
        return next((label for label in labels if any(marker in label.lower() for marker in positive)), None)
    return None


def has_bad_reply(message) -> bool:
    text = str(message.message or "").lower()
    return any(
        marker in text
        for marker in (
            "лимит",
            "уточни действие",
            "not configured",
            "не настроен",
            "не удалось",
            "не смог",
            "отсутствует в plan allowlist",
            "требует подтверждение",
            "должен быть подтверждён",
            " failed",
            "error:",
            "peer должен",
        )
    )


async def send_plain(client, entity, text: str, timeout: int, *, marker: str) -> tuple[Any, int]:
    started = time.perf_counter()
    sent = await client.send_message(entity, text)
    reply = await wait_message(
        client,
        entity,
        after_id=int(sent.id),
        predicate=lambda message: marker.lower() in str(message.message or "").lower(),
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
    marker: str,
) -> tuple[Any, int]:
    started = time.perf_counter()
    sent = await client.send_message(entity, text)
    approval = await wait_message(
        client,
        entity,
        after_id=int(sent.id),
        predicate=lambda message: marker.lower() in str(message.message or "").lower()
        and approval_button(message, approval_text) is not None,
        timeout=timeout,
    )
    await approval.click(text=approval_button(approval, approval_text))
    result = await wait_confirmation_result(client, entity, approval, approval_text, timeout)
    if has_bad_reply(result):
        raise RuntimeError("Confirmed action returned blocked reply")
    return result, int((time.perf_counter() - started) * 1000)


async def run(args, steps: list[Step]) -> None:
    try:
        from telethon import TelegramClient
    except ModuleNotFoundError as error:
        raise RuntimeError("Install Telethon before live E2E") from error
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    username, bot_id = bot_identity(token)
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    with isolated_telethon_session(os.environ["TELEGRAM_USER_SESSION"]) as session:
        client = TelegramClient(session, api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("MTProto session is not authorized")
        entity = await client.get_entity(f"@{username}")
        run_id = uuid.uuid4().hex[:8]
        task_title = f"JarHert E2E {run_id}"
        event_title = f"JarHert Calendar E2E {run_id}"
        try:
            reply, latency = await send_plain(
                client,
                entity,
                f"Ответь ровно: JarHert E2E ping {run_id}",
                args.timeout,
                marker=run_id,
            )
            steps.append(Step("llm_reply", True, latency, int(reply.id)))

            reply, latency = await send_confirmed(
                client,
                entity,
                f"Создай в Trello задачу «{task_title}» в списке Today",
                args.timeout,
                marker=run_id,
            )
            steps.append(Step("trello_create", True, latency, int(reply.id)))
            reply, latency = await send_confirmed(
                client,
                entity,
                f"Удали из Trello задачу «{task_title}»",
                args.timeout,
                marker=run_id,
            )
            steps.append(Step("trello_delete", True, latency, int(reply.id)))

            reply, latency = await send_confirmed(
                client,
                entity,
                f"Создай в Google Calendar событие «{event_title}» на 2030-01-02 с 12:00 до 12:15 по Москве",
                args.timeout,
                marker=run_id,
            )
            steps.append(Step("calendar_create", True, latency, int(reply.id)))
            reply, latency = await send_confirmed(
                client,
                entity,
                f"Удали из Google Calendar событие «{event_title}»",
                args.timeout,
                marker=run_id,
            )
            steps.append(Step("calendar_delete", True, latency, int(reply.id)))

            reply, latency = await send_confirmed(
                client,
                entity,
                f"Экспортируй текст из Telegram peer {bot_id} в TXT, максимум 20 сообщений. Проверка {run_id}",
                args.timeout,
                approval_text="Экспортировать",
                marker=str(bot_id),
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
