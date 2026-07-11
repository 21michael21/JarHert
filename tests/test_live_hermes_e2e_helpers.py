from dataclasses import dataclass
from pathlib import Path
import sqlite3

from scripts.live_hermes_e2e import approval_button, isolated_telethon_session, telethon_session_file


@dataclass
class Button:
    text: str


@dataclass
class Message:
    message: str
    buttons: list[list[Button]]


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
