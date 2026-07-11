from pathlib import Path

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI


def test_hermes_queues_code_for_native_runner_only_in_code_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    with pytest.raises(PermissionError):
        api.coding_job_enqueue(
            mode="coding",
            prompt="Добавь тест",
            repository_url="https://github.com/example/repo",
            idempotency_key="telegram:1:coding",
        )

    api.work_mode_set(mode="code")
    result = api.coding_job_enqueue(
        mode="coding",
        prompt="Добавь тест",
        repository_url="https://github.com/example/repo",
        idempotency_key="telegram:1:coding",
    )

    assert result["status"] == "queued"
    assert result["tg_user_id"] == 566055009
    assert result["idempotency_key"] == "telegram:1:coding"
