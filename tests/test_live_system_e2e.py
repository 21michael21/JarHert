from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from scripts.live_system_e2e import RunReport, StepResult, _real_hermes, evaluate_exit_code, main
from scripts.live_system_telegram import _click_button_and_wait_reply, _has_approval_button, _is_confirmation_reply, _is_transient_ack, _wait_reply


def test_require_live_rejects_skip_blocked_and_fake_provider() -> None:
    report = RunReport(mode="live", run_id="strict-test", started_at="2026-07-09T00:00:00+00:00")
    report.steps.extend(
        [
            StepResult(name="telegram_text", status="passed", provider="fake"),
            StepResult(name="telegram_voice", status="skipped", detail="voice fixture missing"),
            StepResult(name="natural_action", status="failed", blocked_reason="action_blocked"),
        ]
    )

    assert evaluate_exit_code(report, require_live=True) == 1
    assert report.ok is False


@pytest.mark.parametrize(
    "step",
    [
        StepResult(name="voice", status="skipped"),
        StepResult(name="reply", status="passed", blocked_reason="blocked"),
        StepResult(name="provider", status="passed", provider="fake", metadata={"requires_real_provider": True}),
    ],
)
def test_require_live_rejects_each_strict_violation_independently(step: StepResult) -> None:
    report = RunReport(mode="live", run_id="strict-single", started_at="2026-07-09T00:00:00+00:00", steps=[step])

    assert evaluate_exit_code(report, require_live=True) == 1
    assert report.ok is False


def test_local_mode_runs_full_cycle_and_writes_json_report(tmp_path) -> None:
    report_path = tmp_path / "live-system-e2e.json"

    exit_code = main(
        [
            "--mode",
            "local",
            "--tg-user-id",
            "1001",
            "--report-path",
            str(report_path),
        ]
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    steps = {step["name"]: step for step in payload["steps"]}

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["mode"] == "local"
    assert payload["summary"]["failed"] == 0
    assert steps["telegram_text_to_llm"]["trace_id"]
    assert steps["voice_to_natural_action"]["status"] == "passed"
    assert steps["task_approval_callback"]["status"] == "passed"
    assert steps["calendar_action_worker"]["status"] == "passed"
    assert steps["reminder_to_outbox"]["status"] == "passed"
    assert steps["delivery_outbox_final"]["status"] == "passed"
    assert steps["provider_fallback"]["metadata"]["fallback_count"] == 1
    assert steps["duplicate_action_idempotency"]["status"] == "passed"
    assert steps["queued_action_restart"]["status"] == "passed"
    assert steps["monitor_triggered"]["status"] == "passed"
    assert steps["monitor_no_change"]["status"] == "passed"
    assert steps["ownership"]["status"] == "passed"
    assert all(isinstance(step["latency_ms"], int) for step in payload["steps"])


def test_live_reply_waiter_returns_oldest_message_after_checkpoint() -> None:
    @dataclass
    class Message:
        id: int
        out: bool = False

    class Client:
        def iter_messages(self, _entity, *, limit):
            assert limit == 10

            async def messages():
                yield Message(13)
                yield Message(12)
                yield Message(11, out=True)

            return messages()

    reply = asyncio.run(_wait_reply(Client(), "bot", after_id=10, timeout=0.1))

    assert reply.id == 12


def test_live_reply_waiter_skips_fast_ack_until_approval_message() -> None:
    @dataclass
    class Button:
        text: str

    @dataclass
    class Message:
        id: int
        raw_text: str
        buttons: list[list[Button]] | None = None
        out: bool = False

    class Client:
        def iter_messages(self, _entity, *, limit):
            assert limit == 10

            async def messages():
                yield Message(14, "Нужно подтверждение", [[Button("Подтвердить")]])
                yield Message(13, "Принял, обрабатываю. Итог пришлю отдельным сообщением.")

            return messages()

    reply = asyncio.run(
        _wait_reply(
            Client(),
            "bot",
            after_id=10,
            timeout=0.1,
            predicate=_has_approval_button,
        )
    )

    assert reply.id == 14
    assert _is_transient_ack(Message(13, "Принял, обрабатываю. Итог пришлю отдельным сообщением."))


def test_live_callback_click_retries_until_confirmation(monkeypatch) -> None:
    @dataclass
    class Message:
        id: int
        raw_text: str
        out: bool = False

    class Button:
        def __init__(self) -> None:
            self.clicks = 0

        async def click(self) -> None:
            self.clicks += 1

    button = Button()

    class Client:
        def iter_messages(self, _entity, *, limit):
            assert limit == 10

            async def messages():
                if button.clicks >= 2:
                    yield Message(16, "Подтвердил Job #20: 1 действий. Выполняю по порядку.")

            return messages()

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("scripts.live_system_telegram.asyncio.sleep", no_sleep)

    reply = asyncio.run(
        _click_button_and_wait_reply(
            Client(),
            "bot",
            button,
            after_id=15,
            timeout=0.05,
            predicate=_is_confirmation_reply,
            attempt_timeout=0.01,
        )
    )

    assert button.clicks == 2
    assert reply.id == 16


def test_real_hermes_uses_e2e_session_factory(monkeypatch) -> None:
    import backend.config
    import gateway_bot.main

    isolated_factory = object()
    expected_client = object()
    called = {}
    monkeypatch.setattr(backend.config, "Settings", lambda: SimpleNamespace(hermes_mode="openai_router"))

    def build_client(*, session_factory):
        called["session_factory"] = session_factory
        return expected_client

    monkeypatch.setattr(gateway_bot.main, "build_hermes_client", build_client)

    client = _real_hermes(RunReport("live", "run", "now"), isolated_factory)

    assert client is expected_client
    assert called["session_factory"] is isolated_factory
