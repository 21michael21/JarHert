import asyncio
from dataclasses import dataclass
from pathlib import Path
import sqlite3

import pytest

from scripts.live_hermes_e2e import (
    approval_button,
    cleanup_temporary_calendar_event,
    cleanup_temporary_task,
    is_transient_confirmation_ack,
    is_transient_confirmation_update,
    isolated_telethon_session,
    recent_inbound_messages,
    require_live_approval,
    task_present,
    task_adapter_from_profile,
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


def test_approval_button_accepts_numbered_generic_confirmation() -> None:
    message = Message(
        message="Подтвердить создание временной задачи?",
        buttons=[[Button("1"), Button("✏️ Other (type answer)")]],
    )

    assert approval_button(message, "Выполнить") == "1"


def test_approval_button_supports_native_mcp_elicitation() -> None:
    message = Message(
        message="Выполнить этот план?",
        buttons=[[Button("Approve Once"), Button("Decline")]],
    )

    assert approval_button(message, "Выполнить") == "Approve Once"


def test_live_runner_does_not_treat_telegram_callback_acknowledgement_as_a_tool_result() -> None:
    acknowledgement = Message("✅ Approved once by A Jolly", [], id=43)
    completed = Message("Готово: задача создана.", [], id=44)

    assert is_transient_confirmation_ack(acknowledgement) is True
    assert is_transient_confirmation_ack(completed) is False


def test_live_runner_ignores_progress_updates_until_the_tool_has_finished() -> None:
    progress = Message("⏳ Working — выполняю задачу", [], id=43)
    accepted = Message("Принял, обрабатываю.", [], id=44)
    completed = Message("Готово: задача создана.", [], id=45)

    assert is_transient_confirmation_update(progress) is True
    assert is_transient_confirmation_update(accepted) is True
    assert is_transient_confirmation_update(completed) is False


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


def test_confirmation_result_waits_for_the_verified_side_effect() -> None:
    approval = Message("Выполнить этот план?", [[Button("Выполнить")]], id=42)
    completed = Message("Готово: задача создана.", [], id=43)
    checks = 0

    class Client:
        async def get_messages(self, entity, ids=None, limit=None):
            if ids is not None:
                return approval
            return [completed]

    def side_effect_completed() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    result = asyncio.run(
        wait_confirmation_result(
            Client(),
            "bot",
            approval,
            "Выполнить",
            timeout=2,
            completion=side_effect_completed,
        )
    )

    assert result is completed
    assert checks >= 2


def test_recent_messages_times_out_without_hanging_the_runner() -> None:
    class Client:
        async def get_messages(self, entity, limit):
            await asyncio.sleep(1)

    assert asyncio.run(recent_inbound_messages(Client(), "bot", timeout=0.01)) == []


def test_live_runner_requires_explicit_external_action_flag() -> None:
    with pytest.raises(PermissionError, match="--allow-live"):
        require_live_approval(False)

    require_live_approval(True)


def test_live_runner_task_verification_and_cleanup_are_side_effect_bounded() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.tasks = {"JarHert E2E test"}

        def list_tasks(self) -> str:
            return "\n".join(self.tasks)

        def delete_task(self, *, title: str) -> None:
            self.tasks.discard(title)

    adapter = Adapter()

    assert task_present(adapter, "JarHert E2E test") is True
    cleanup_temporary_task(adapter, "JarHert E2E test")
    assert task_present(adapter, "JarHert E2E test") is False
    cleanup_temporary_task(adapter, "already-gone")


def test_live_runner_calendar_cleanup_is_best_effort() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.events = {"JarHert Calendar E2E test"}

        def delete_calendar_event(self, *, title: str) -> None:
            self.events.discard(title)

    adapter = Adapter()
    cleanup_temporary_calendar_event(adapter, "JarHert Calendar E2E test")
    cleanup_temporary_calendar_event(adapter, "already-gone")

    assert adapter.events == set()


def test_live_runner_loads_native_adapter_from_profile_path(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "profile"
    package = profile / "native_tools"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "task_calendar.py").write_text(
        "class TaskCalendarAdapter:\n"
        "    @classmethod\n"
        "    def from_env(cls):\n"
        "        return 'adapter-from-profile'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile))

    assert task_adapter_from_profile() == "adapter-from-profile"
