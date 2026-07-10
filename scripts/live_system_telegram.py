from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable


async def run_live_telegram(*, args, run_id: str) -> list[dict]:
    try:
        from telethon import TelegramClient
    except ModuleNotFoundError as error:
        raise RuntimeError("telethon is required for --mode live") from error
    from backend.config import Settings

    settings = Settings()
    bot = args.bot_username or _bot_username(settings.bot_token)
    client = TelegramClient(args.telethon_session, int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(f"Telethon session is not authorized: {args.telethon_session}")
        current_user = await client.get_me()
        if current_user is None or current_user.id != args.tg_user_id:
            raise RuntimeError(f"Telethon session user does not match --tg-user-id={args.tg_user_id}")
        results = [await _exchange(client, bot, "live_telegram_text_llm", f"/ask ответь одним предложением: e2e {run_id}", args.live_timeout)]
        results.append(await _voice_action_exchange(client, bot, args.voice_file, run_id, args.live_timeout))
        results.append(await _exchange(client, bot, "live_task_approval_delivery", f"/task e2e-{run_id} | list=Inbox", args.live_timeout, approve=True, followup=True))
        day = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        results.append(await _exchange(client, bot, "live_calendar_approval_delivery", f"/calendar e2e-{run_id} | start={day} 10:00 | end={day} 10:30", args.live_timeout, approve=True, followup=True))
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        results.append(await _exchange(client, bot, "live_reminder_delivery", f"/remind {due} e2e-{run_id}", args.live_timeout, followup=True))
        return results
    finally:
        await client.disconnect()


async def _exchange(client, bot: str, name: str, text: str, timeout: float, *, approve: bool = False, followup: bool = False) -> dict:
    started = time.perf_counter()
    sent = await client.send_message(bot, text)
    if approve:
        reply = await _wait_reply(client, bot, sent.id, timeout, predicate=_has_approval_button)
        buttons = [button for row in (reply.buttons or []) for button in row]
        button = next((item for item in buttons if "Подтверд" in item.text), None)
        if button is None:
            raise AssertionError("approval inline button missing")
        reply = await _click_button_and_wait_reply(client, bot, button, reply.id, timeout, predicate=_is_confirmation_reply)
    else:
        reply = await _wait_reply(client, bot, sent.id, timeout, predicate=_is_meaningful_reply)
    if followup:
        reply = await _wait_reply(client, bot, reply.id, timeout, predicate=_is_meaningful_reply)
    return _result(name, started, reply)


async def _voice_action_exchange(client, bot: str, voice_file: str, run_id: str, timeout: float) -> dict:
    started = time.perf_counter()
    sent = await client.send_file(bot, voice_file, voice_note=True, caption=f"e2e {run_id}")
    transcript_reply = await _wait_reply(
        client,
        bot,
        sent.id,
        timeout,
        predicate=lambda message: "Расшифровал:" in (message.raw_text or "") and _has_approval_button(message),
    )
    buttons = [button for row in (transcript_reply.buttons or []) for button in row]
    approval = next((item for item in buttons if "Подтверд" in item.text), None)
    if "Расшифровал:" not in (transcript_reply.raw_text or "") or approval is None:
        return _result("live_telegram_voice_stt_action", started, transcript_reply, required_text="Расшифровал:", forced_block="voice_action_queue_missing")
    callback_reply = await _click_button_and_wait_reply(client, bot, approval, transcript_reply.id, timeout, predicate=_is_confirmation_reply)
    final_reply = await _wait_reply(client, bot, callback_reply.id, timeout, predicate=_is_meaningful_reply)
    result = _result("live_telegram_voice_stt_action", started, final_reply)
    result["metadata"]["transcript_message_id"] = transcript_reply.id
    result["metadata"]["approval_message_id"] = callback_reply.id
    return result


def _result(name: str, started: float, reply, *, required_text: str = "", forced_block: str = "") -> dict:
    blocked = bool(forced_block) or _looks_blocked(reply.raw_text) or bool(required_text and required_text not in (reply.raw_text or ""))
    return {
        "name": name,
        "status": "failed" if blocked else "passed",
        "detail": "blocked Telegram reply" if blocked else "",
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "blocked_reason": forced_block or ("blocked_reply" if blocked else ""),
        "metadata": {"message_id": reply.id, "scope": "telegram_live"},
    }


async def _wait_reply(
    client,
    entity,
    after_id: int,
    timeout: float,
    *,
    predicate: Callable[[object], bool] | None = None,
):
    deadline = time.monotonic() + timeout
    checkpoint = after_id
    while time.monotonic() < deadline:
        candidates = []
        async for message in client.iter_messages(entity, limit=10):
            if message.id > checkpoint and not message.out:
                candidates.append(message)
        if candidates:
            for message in sorted(candidates, key=lambda item: item.id):
                checkpoint = max(checkpoint, message.id)
                if predicate is None or predicate(message):
                    return message
        await asyncio.sleep(1)
    raise TimeoutError(f"Telegram reply timeout after message {after_id}")


async def _click_button_and_wait_reply(
    client,
    entity,
    button,
    after_id: int,
    timeout: float,
    *,
    predicate: Callable[[object], bool],
    attempts: int = 2,
    attempt_timeout: float = 12,
):
    deadline = time.monotonic() + timeout
    last_error: TimeoutError | None = None
    for _ in range(max(1, attempts)):
        if time.monotonic() >= deadline:
            break
        await button.click()
        remaining = max(0.1, deadline - time.monotonic())
        try:
            return await _wait_reply(client, entity, after_id, min(attempt_timeout, remaining), predicate=predicate)
        except TimeoutError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise TimeoutError(f"Telegram reply timeout after message {after_id}")


def _has_approval_button(message) -> bool:
    return any("Подтверд" in button.text for row in (message.buttons or []) for button in row)


def _is_transient_ack(message) -> bool:
    text = " ".join((message.raw_text or "").split()).lower()
    return text.startswith("принял") and any(
        marker in text
        for marker in ("обрабатываю", "расшифровываю", "выполняю подтверждённое", "итог пришлю")
    )


def _is_meaningful_reply(message) -> bool:
    return not _is_transient_ack(message)


def _is_confirmation_reply(message) -> bool:
    text = " ".join((message.raw_text or "").split()).lower()
    return text.startswith("подтвердил job #") or text.startswith("подтвердил action #")


def _bot_username(token: str) -> str:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as response:
        payload = json.load(response)
    username = str(payload.get("result", {}).get("username") or "")
    if not username:
        raise RuntimeError("Telegram getMe returned no bot username")
    return username


def _looks_blocked(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in ("не подключ", "не выполнил", "не смог", "ошибка", "закрыт", "не настро", "слишком больш"))
