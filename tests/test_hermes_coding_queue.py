from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


def test_hermes_queues_previewed_code_for_the_native_runner_from_fast_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    result = api.coding_job_enqueue(
        mode="coding",
        prompt="Добавь тест",
        repository_url="https://github.com/example/repo",
        idempotency_key="telegram:1:coding",
    )

    assert result["status"] == "queued"
    assert result["tg_user_id"] == 566055009
    assert result["idempotency_key"] == "telegram:1:coding"
