from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.live_hermes_e2e import (
    approval_button,
    bot_identity,
    has_bad_reply,
    isolated_telethon_session,
    load_env,
    wait_confirmation_result,
    wait_message,
)

PERSONAL_QUEUE_SSH = "deploy@89.124.124.212"


def require_personal_queue(queue_ssh: str) -> str:
    if queue_ssh != PERSONAL_QUEUE_SSH:
        raise SystemExit(
            f"Refusing coding queue outside the pinned personal VPS: {queue_ssh or 'missing'}"
        )
    return queue_ssh


@dataclass
class LiveCodingReport:
    ok: bool
    marker: str
    accepted_message_id: int | None
    result_message_id: int | None
    enqueue_latency_ms: int
    runner_latency_ms: int
    delivery_latency_ms: int
    error: str | None


def require_live_approval(allowed: bool) -> None:
    if not allowed:
        raise PermissionError("Pass --allow-live: this check sends one Telegram coding job to the private queue.")


def coding_request(marker: str) -> str:
    filename = f"CODEX_CANARY_{marker}.md"
    return (
        "Поставь coding job: в публичной репе https://github.com/octocat/Hello-World "
        f"создай в Docker sandbox файл {filename} с одной строкой `sandbox canary`. "
        "Покажи настоящий git diff и выполни доступную проверку. "
        "Не делай commit, push, merge или deploy."
    )


def has_coding_evidence(text: str, marker: str) -> bool:
    normalized = text.lower()
    return marker.lower() in normalized and "diff" in normalized


async def enqueue_job(*, session: str, timeout: int, marker: str) -> tuple[int, int]:
    from telethon import TelegramClient

    username, _ = bot_identity(os.environ["TELEGRAM_BOT_TOKEN"])
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    with isolated_telethon_session(session) as isolated:
        client = TelegramClient(isolated, api_id, api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("MTProto session is not authorized")
            entity = await client.get_entity(f"@{username}")
            started = time.perf_counter()
            sent = await client.send_message(entity, coding_request(marker))
            approval = await wait_message(
                client,
                entity,
                after_id=int(sent.id),
                predicate=lambda message: approval_button(message, "Выполнить") is not None,
                timeout=timeout,
            )
            button = approval_button(approval, "Выполнить")
            if button is None:
                raise RuntimeError("Coding job did not return one approval control")
            await approval.click(text=button)
            accepted = await wait_confirmation_result(client, entity, approval, "Выполнить", timeout)
            if has_bad_reply(accepted):
                raise RuntimeError("Coding job approval returned a blocked reply")
            return int(accepted.id), int((time.perf_counter() - started) * 1000)
        finally:
            await client.disconnect()


def run_local_runner(*, queue_ssh: str, timeout: int) -> int:
    queue_ssh = require_personal_queue(queue_ssh)
    started = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/coding_runner.py",
            "--once",
            "--queue-ssh",
            queue_ssh,
            "--worker-id",
            "live-coding-canary",
        ],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Local coding runner failed before it could return a queue result")
    return int((time.perf_counter() - started) * 1000)


def dispatch_result(*, queue_ssh: str, timeout: int) -> None:
    queue_ssh = require_personal_queue(queue_ssh)
    remote_command = (
        "HERMES_HOME=/home/deploy/.hermes/profiles/jarhert "
        "/home/deploy/.hermes/profiles/jarhert/.venv/bin/python "
        "/home/deploy/.hermes/profiles/jarhert/scripts/dispatch_coding_results.py"
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", queue_ssh, remote_command],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("VDS could not dispatch the coding result to Telegram")


async def wait_for_result(*, session: str, after_id: int, marker: str, timeout: int) -> int:
    from telethon import TelegramClient

    username, _ = bot_identity(os.environ["TELEGRAM_BOT_TOKEN"])
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    with isolated_telethon_session(session) as isolated:
        client = TelegramClient(isolated, api_id, api_hash)
        await client.connect()
        try:
            entity = await client.get_entity(f"@{username}")
            result = await wait_message(
                client,
                entity,
                after_id=after_id,
                predicate=lambda message: marker.lower() in str(message.message or "").lower(),
                timeout=timeout,
            )
            text = str(result.message or "").lower()
            if has_bad_reply(result) or "не выполнилась" in text or not has_coding_evidence(text, marker):
                raise RuntimeError("Coding job delivered a failed or blocked result")
            return int(result.id)
        finally:
            await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Telegram -> Docker coding-job canary.")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--profile-home", type=Path, default=Path.home() / ".hermes" / "profiles" / "jarhert")
    parser.add_argument("--telethon-env", type=Path, required=True)
    parser.add_argument("--telethon-session", required=True)
    parser.add_argument("--queue-ssh", default=PERSONAL_QUEUE_SSH)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--runner-timeout", type=int, default=960)
    parser.add_argument("--report", type=Path, default=Path("reports/live_coding_job.json"))
    args = parser.parse_args()
    args.queue_ssh = require_personal_queue(args.queue_ssh)

    report = LiveCodingReport(False, "", None, None, 0, 0, 0, None)
    try:
        require_live_approval(args.allow_live)
        load_env(args.profile_home / ".env")
        load_env(args.telethon_env)
        marker = uuid.uuid4().hex[:8]
        report.marker = marker
        accepted_id, report.enqueue_latency_ms = asyncio.run(
            enqueue_job(session=args.telethon_session, timeout=args.timeout, marker=marker)
        )
        report.accepted_message_id = accepted_id
        report.runner_latency_ms = run_local_runner(queue_ssh=args.queue_ssh, timeout=args.runner_timeout)
        started = time.perf_counter()
        dispatch_result(queue_ssh=args.queue_ssh, timeout=args.timeout)
        report.result_message_id = asyncio.run(
            wait_for_result(
                session=args.telethon_session,
                after_id=accepted_id,
                marker=marker,
                timeout=args.timeout,
            )
        )
        report.delivery_latency_ms = int((time.perf_counter() - started) * 1000)
        report.ok = True
    except Exception as error:
        report.error = f"{type(error).__name__}: {error}"

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report.ok, "report": str(args.report), "marker": report.marker}, ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
