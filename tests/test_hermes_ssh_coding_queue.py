from __future__ import annotations

import subprocess
from pathlib import Path

from hermes.native_tools.ssh_coding_queue import SshNativeCodingQueueClient
from hermes.scripts.coding_queue_cli import dispatch


def test_private_queue_cli_claims_and_completes_without_http(tmp_path: Path) -> None:
    database_path = tmp_path / "personal-os.sqlite3"
    from hermes.native_tools.coding_jobs import NativeCodingJobStore

    store = NativeCodingJobStore(database_path)
    job = store.enqueue(
        tg_user_id=1,
        mode="coding",
        prompt="Добавь тест",
        idempotency_key="telegram:queue:1",
    )

    claimed = dispatch("claim", {"worker_id": "mac-main"}, database_path=database_path)
    completed = dispatch(
        "complete",
        {"job_id": job.id, "worker_id": "mac-main", "result_text": "tests passed"},
        database_path=database_path,
    )

    assert claimed["id"] == job.id
    assert completed["status"] == "succeeded"
    assert dispatch("ping", {}, database_path=database_path) == {"ok": True}


def test_ssh_client_sends_json_over_stdin_to_fixed_profile_cli() -> None:
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"id": 8, "mode": "coding"}', stderr="")

    client = SshNativeCodingQueueClient("deploy@example.test", execute=execute)
    claimed = client.claim("mac-main")

    assert claimed == {"id": 8, "mode": "coding"}
    argv, kwargs = calls[0]
    assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "-o"]
    host_index = argv.index("deploy@example.test")
    assert "ControlMaster=auto" in argv
    assert any("ControlPersist" in part for part in argv)
    assert "coding_queue_cli.py claim" in argv[host_index + 1]
    assert kwargs["input"] == '{"worker_id": "mac-main"}'


def test_ssh_client_ping_does_not_claim_a_job() -> None:
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")

    assert SshNativeCodingQueueClient("deploy@example.test", execute=execute).ping() is True
    argv = calls[0][0]
    host_index = argv.index("deploy@example.test")
    assert "coding_queue_cli.py ping" in argv[host_index + 1]
    assert calls[0][1]["input"] == "{}"
