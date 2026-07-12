from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
if __package__ in {None, ""}:
    sys.path.insert(0, str(hermes_home))
    from native_tools.coding_jobs import NativeCodingJobStore
    from native_tools.mcp_api import personal_os_database_path
else:
    from ..native_tools.coding_jobs import NativeCodingJobStore
    from ..native_tools.mcp_api import personal_os_database_path


def dispatch(operation: str, payload: dict[str, object], *, database_path: Path) -> object:
    store = NativeCodingJobStore(database_path)
    if operation == "ping":
        return {"ok": True}
    if operation == "claim":
        job = store.claim_next(worker_id=str(payload["worker_id"]))
        return asdict(job) if job else None
    if operation == "heartbeat":
        return {"ok": store.heartbeat(int(payload["job_id"]), worker_id=str(payload["worker_id"]))}
    if operation == "complete":
        return asdict(
            store.complete(
                int(payload["job_id"]),
                worker_id=str(payload["worker_id"]),
                result_text=str(payload["result_text"]),
            )
        )
    if operation == "fail":
        return asdict(
            store.fail(
                int(payload["job_id"]),
                worker_id=str(payload["worker_id"]),
                error=str(payload["error"]),
            )
        )
    raise ValueError("Unsupported coding queue operation.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Private stdin/stdout API for the native Hermes coding queue.")
    parser.add_argument("operation", choices=("ping", "claim", "heartbeat", "complete", "fail"))
    args = parser.parse_args()
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object.")
        result = dispatch(args.operation, payload, database_path=personal_os_database_path())
    except (KeyError, TypeError, ValueError, PermissionError) as error:
        print(json.dumps({"error": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
