from __future__ import annotations

import os
import runpy
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "deploy" / "vps" / "require_personal_vps.sh"
PERSONAL_TARGET = "deploy@89.124.124.212"
PERSONAL_FINGERPRINT = "SHA256:cwG4kRQQsY4OlMsRPSzn3Y1FuKVCEmkmvaQ6b0KPEBQ"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _guard_env(
    tmp_path: Path,
    *,
    role: str = "jarhert-personal-vps-v1",
    host: str = "jarhert",
    ip: str = "89.124.124.212",
    fingerprint: str = PERSONAL_FINGERPRINT,
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    _write_executable(
        bin_dir / "ssh-keyscan",
        'printf "%s\\n" "89.124.124.212 ssh-ed25519 TEST_PUBLIC_KEY"',
    )
    _write_executable(
        bin_dir / "ssh-keygen",
        f'cat >/dev/null; printf "%s\\n" "256 {fingerprint} 89.124.124.212 (ED25519)"',
    )
    _write_executable(
        bin_dir / "ssh",
        "printf 'host=" + host + "\\nip=" + ip + "\\nrole=" + role + "\\n'",
    )
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_guard(
    tmp_path: Path,
    target: str,
    *,
    role: str = "jarhert-personal-vps-v1",
    host: str = "jarhert",
    ip: str = "89.124.124.212",
    fingerprint: str = PERSONAL_FINGERPRINT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "$1"; require_personal_vps_remote "$2"', "guard-test", str(GUARD), target],
        cwd=ROOT,
        env=_guard_env(tmp_path, role=role, host=host, ip=ip, fingerprint=fingerprint),
        check=False,
        capture_output=True,
        text=True,
    )


def test_guard_rejects_any_unpinned_target_before_ssh(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, "root@203.0.113.10")

    assert result.returncode == 2
    assert "Refusing JarHert operation on unpinned VPS" in result.stderr


def test_guard_requires_the_personal_server_identity(tmp_path: Path) -> None:
    accepted = _run_guard(tmp_path / "accepted", PERSONAL_TARGET)
    wrong_role = _run_guard(tmp_path / "wrong-role", PERSONAL_TARGET, role="work-server")

    assert accepted.returncode == 0, accepted.stderr
    assert "personal_vps_guard=ok" in accepted.stdout
    assert wrong_role.returncode == 2
    assert "role marker mismatch" in wrong_role.stderr


@pytest.mark.parametrize(
    ("identity", "expected_error"),
    (
        ({"host": "another-host"}, "hostname mismatch"),
        ({"ip": "203.0.113.10"}, "IP mismatch"),
        ({"fingerprint": "SHA256:not-the-personal-key"}, "SSH host key mismatch"),
    ),
)
def test_guard_rejects_mismatched_server_identity(
    tmp_path: Path,
    identity: dict[str, str],
    expected_error: str,
) -> None:
    result = _run_guard(tmp_path, PERSONAL_TARGET, **identity)

    assert result.returncode == 2
    assert expected_error in result.stderr


def test_every_remote_mutation_script_calls_the_guard_first() -> None:
    remote_scripts = (
        "install_backup_timer.sh",
        "install_channel_digest_timer.sh",
        "install_coding_dispatch_timer.sh",
        "install_github_mcp_readonly.sh",
        "install_personal_summary_timers.sh",
        "install_telegram_export_cleanup_timer.sh",
        "install_watchdog_timer.sh",
        "sync_hermes_profile.sh",
        "sync_task_command_center.sh",
        "verify_single_telegram_gateway.sh",
    )
    for name in remote_scripts:
        script = (ROOT / "deploy" / "vps" / name).read_text(encoding="utf-8")
        guard_call = 'require_personal_vps_remote "$REMOTE"'
        assert guard_call in script, name
        first_transport = min(
            position for token in ('ssh "$REMOTE"', 'scp ', 'rsync ') if (position := script.find(token)) >= 0
        )
        assert script.index(guard_call) < first_transport, name


def test_server_local_installers_require_the_role_marker() -> None:
    for name in ("install_dashboard_https.sh", "install_dashboard_service.sh"):
        script = (ROOT / "deploy" / "vps" / name).read_text(encoding="utf-8")
        assert "require_personal_vps_local" in script, name


def test_role_bootstrap_checks_the_pinned_endpoint_before_writing() -> None:
    script = (ROOT / "deploy" / "vps" / "bootstrap_personal_vps_guard.sh").read_text(encoding="utf-8")

    assert script.index('verify_personal_vps_endpoint "$REMOTE"') < script.index('ssh "$REMOTE"')


def test_repository_instructions_pin_jarhert_to_the_personal_vps() -> None:
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert PERSONAL_TARGET in instructions
    assert "Never deploy, install, copy, synchronize, start, or configure JarHert" in instructions


def test_coding_entrypoints_pin_the_personal_queue() -> None:
    for name in ("coding_runner.py", "live_coding_job.py"):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "PERSONAL_QUEUE_SSH = \"deploy@89.124.124.212\"" in script, name
        assert "require_personal_queue" in script, name


@pytest.mark.parametrize("name", ("coding_runner.py", "live_coding_job.py"))
def test_coding_entrypoints_reject_another_queue(name: str) -> None:
    module = runpy.run_path(str(ROOT / "scripts" / name), run_name=f"test_{name}")

    assert module["require_personal_queue"](PERSONAL_TARGET) == PERSONAL_TARGET
    with pytest.raises(SystemExit, match="pinned personal VPS"):
        module["require_personal_queue"]("deploy@203.0.113.10")
