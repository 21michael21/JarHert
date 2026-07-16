from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - production dependency.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")

from hermes.native_tools.sandbox_worker import SandboxTask, coding_worker_from_environment
from hermes.native_tools.ssh_coding_queue import SshNativeCodingQueueClient


def run_once(*, client, worker, worker_id: str) -> bool:
    payload = client.claim(worker_id)
    if not payload:
        return False
    job_id = int(payload["id"])
    stop = threading.Event()
    heartbeat = None
    if hasattr(client, "heartbeat"):
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(client, job_id, worker_id, stop),
            daemon=True,
        )
        heartbeat.start()
    try:
        prompt = _prompt_with_predecessor_result(
            str(payload["prompt"]),
            payload.get("predecessor_result"),
        )
        result = worker.run(SandboxTask(
            mode=str(payload["mode"]),
            prompt=prompt,
            repository_url=payload.get("repository_url"),
            source_urls=tuple(payload.get("source_urls") or []),
            source_text=payload.get("source_text"),
            source_label=payload.get("source_label"),
        ))
    except Exception as error:
        client.fail(job_id, worker_id, _queue_failure_reason(error))
    else:
        client.complete(job_id, worker_id, result.output)
    finally:
        stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=1)
    return True


def _heartbeat_loop(client, job_id: int, worker_id: str, stop: threading.Event) -> None:
    while not stop.wait(30):
        try:
            client.heartbeat(job_id, worker_id)
        except Exception:
            return


def _queue_failure_reason(error: Exception) -> str:
    """Keep actionable worker diagnostics without persisting credentials."""
    detail = str(error).strip() or type(error).__name__
    detail = re.sub(
        r"(?i)\b(?:sk|gh[pousr])[-_][A-Za-z0-9_-]+\b",
        "<redacted>",
        detail,
    )
    return detail[:500]


def _prompt_with_predecessor_result(prompt: str, predecessor_result: object) -> str:
    """Give a queued follow-up only the previous step's bounded, local result."""
    previous = str(predecessor_result or "").strip()
    if not previous:
        return prompt
    return f"{prompt}\n\nРезультат предыдущего шага:\n{previous[:12_000]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run sandboxed Hermes coding jobs from a remote queue.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true", help="Verify SSH queue and the selected local coding executor without claiming a job.")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--worker-id", default=f"mac-{socket.gethostname()}")
    parser.add_argument(
        "--queue-ssh",
        default=os.getenv("HERMES_CODING_QUEUE_SSH", ""),
        help="SSH destination for the private Hermes profile queue, for example deploy@vps.",
    )
    parser.add_argument("--remote-profile", default="/home/deploy/.hermes/profiles/jarhert")
    parser.add_argument("--remote-python", default="/home/deploy/.hermes/hermes-agent/venv/bin/python")
    parser.add_argument(
        "--executor",
        choices=("codex", "hermes"),
        default=os.getenv("HERMES_CODING_EXECUTOR", "codex"),
        help="Local executor: Codex workspace sandbox by default, or the legacy Hermes Docker profile.",
    )
    args = parser.parse_args()
    if not args.queue_ssh:
        raise SystemExit("Set --queue-ssh or HERMES_CODING_QUEUE_SSH for the native Hermes queue.")
    client = SshNativeCodingQueueClient(
        args.queue_ssh,
        remote_profile=args.remote_profile,
        remote_python=args.remote_python,
    )
    os.environ["HERMES_CODING_EXECUTOR"] = args.executor
    worker = coding_worker_from_environment()
    worker.preflight()
    if args.check:
        client.ping()
        print("coding_runner_ready=true")
        return 0
    while True:
        worked = run_once(client=client, worker=worker, worker_id=args.worker_id)
        if args.once:
            return 0
        if not worked:
            time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
