from __future__ import annotations

from pathlib import Path


def test_task_command_center_sync_requires_explicit_secret_copy_and_never_uses_git() -> None:
    script = (Path(__file__).resolve().parents[1] / "deploy" / "vps" / "sync_task_command_center.sh").read_text(
        encoding="utf-8"
    )

    assert "TASK_COMMAND_CENTER_COPY_SECRETS" in script
    assert "TASK_COMMAND_CENTER_COPY_SECRETS=1" in script
    assert "client_secret.json" in script
    assert "token.json" in script
    assert "git add" not in script
    assert "git commit" not in script
