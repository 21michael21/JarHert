from __future__ import annotations

import argparse
import os
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

from hermes.native_tools.coding_queue import RemoteCodingQueueClient
from hermes.native_tools.sandbox_worker import SandboxTask, SandboxedHermesWorker
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
        result = worker.run(SandboxTask(
            mode=str(payload["mode"]),
            prompt=str(payload["prompt"]),
            repository_url=payload.get("repository_url"),
            source_urls=tuple(payload.get("source_urls") or []),
        ))
    except Exception as error:
        client.fail(job_id, worker_id, type(error).__name__)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run sandboxed Hermes coding jobs from a remote queue.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--worker-id", default=f"mac-{socket.gethostname()}")
    parser.add_argument("--queue-ssh", default=os.getenv("HERMES_CODING_QUEUE_SSH", ""))
    parser.add_argument("--remote-profile", default="/home/deploy/.hermes/profiles/jarhert")
    parser.add_argument("--remote-python", default="/home/deploy/.hermes/hermes-agent/venv/bin/python")
    args = parser.parse_args()
    if args.queue_ssh:
        client = SshNativeCodingQueueClient(
            args.queue_ssh,
            remote_profile=args.remote_profile,
            remote_python=args.remote_python,
        )
    else:
        base_url = os.getenv("JARHERT_BACKEND_URL", "").strip()
        token = os.getenv("ASSISTANT_SERVICE_TOKEN", "").strip()
        if not base_url or not token:
            raise SystemExit("Set --queue-ssh or JARHERT_BACKEND_URL and ASSISTANT_SERVICE_TOKEN")
        client = RemoteCodingQueueClient(base_url, token)
    worker = SandboxedHermesWorker()
    while True:
        worked = run_once(client=client, worker=worker, worker_id=args.worker_id)
        if args.once:
            return 0
        if not worked:
            time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
