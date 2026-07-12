import asyncio
from dataclasses import dataclass
from pathlib import Path
import sqlite3

import pytest

from scripts.live_hermes_e2e import (
    approval_button,
    isolated_telethon_session,
    recent_inbound_messages,
    require_live_approval,
    telethon_session_file,
    wait_confirmation_result,
)


@dataclass
class Button:
    text: str


@dataclass
class Message:
    message: str
    buttons: list[list[Button]]
    id: int = 1
    out: bool = False


def test_approval_button_supports_numbered_telegram_clarify_buttons() -> None:
    message = Message(
        message="1. Выполнить\n2. Отмена",
        buttons=[[Button("1"), Button("2"), Button("✏️ Other (type answer)")]],
    )

    assert approval_button(message, "Выполнить") == "1"
    assert approval_button(message, "Экспортировать") is None


def test_approval_button_supports_native_mcp_elicitation() -> None:
    message = Message(
        message="Выполнить этот план?",
        buttons=[[Button("Approve Once"), Button("Decline")]],
    )

    assert approval_button(message, "Выполнить") == "Approve Once"


def test_isolated_telethon_session_uses_a_disposable_sqlite_snapshot(tmp_path) -> None:
    source = tmp_path / "owner.session"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sessions (value TEXT)")
        connection.execute("INSERT INTO sessions VALUES ('authorized')")

    assert telethon_session_file(str(tmp_path / "owner")) == source
    with isolated_telethon_session(str(tmp_path / "owner")) as snapshot:
        assert snapshot != str(source)
        with sqlite3.connect(snapshot) as connection:
            assert connection.execute("SELECT value FROM sessions").fetchone()[0] == "authorized"

    assert not Path(snapshot).exists()


def test_confirmation_result_accepts_an_edited_approval_message() -> None:
    approval = Message("Выполнить этот план?", [[Button("Выполнить")]], id=42)
    edited = Message("Готово.", [], id=42)

    class Client:
        async def get_messages(self, entity, ids):
            assert entity == "bot"
            assert ids == 42
            return edited

        async def iter_messages(self, entity, limit):
            if False:
                yield None

    result = asyncio.run(wait_confirmation_result(Client(), "bot", approval, "Выполнить", timeout=1))
    assert result is edited


def test_recent_messages_times_out_without_hanging_the_runner() -> None:
    class Client:
        async def get_messages(self, entity, limit):
            await asyncio.sleep(1)

    assert asyncio.run(recent_inbound_messages(Client(), "bot", timeout=0.01)) == []


def test_live_runner_requires_explicit_external_action_flag() -> None:
    with pytest.raises(PermissionError, match="--allow-live"):
        require_live_approval(False)

    require_live_approval(True)
