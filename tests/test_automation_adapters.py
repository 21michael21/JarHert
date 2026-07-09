from __future__ import annotations

import asyncio

from assistant.action_queue import ActionStatus
from assistant.action_schema import ActionType
from assistant.action_worker import ActionWorkerAdapter
from assistant.automation_runtime import AutomationRuntime
from assistant.delivery_outbox import DeliveryOutboxAdapter
from assistant.monitors.runner import MonitorWorkerAdapter
from assistant.telegram_trends import TelegramTrendWorkerAdapter
from backend.automation_store import SqlAutomationLeaseStore
from backend.db import init_db, make_session_factory
from backend.stores import SqlActionQueueStore, UserStore
from gateway_bot import telegram_workers
from reminders.worker import ReminderWorkerAdapter


def test_background_workers_expose_unique_runtime_adapters() -> None:
    adapter_classes = [
        ActionWorkerAdapter,
        DeliveryOutboxAdapter,
        ReminderWorkerAdapter,
        MonitorWorkerAdapter,
        TelegramTrendWorkerAdapter,
    ]

    assert [adapter.name for adapter in adapter_classes] == [
        "actions",
        "delivery_outbox",
        "reminders",
        "monitors",
        "telegram_trends",
    ]
    assert all(adapter.default_policy.timeout_seconds > 0 for adapter in adapter_classes)
    assert all(adapter.default_policy.lease_seconds > adapter.default_policy.heartbeat_seconds for adapter in adapter_classes)


def test_gateway_builds_one_runtime_for_all_inline_workers(tmp_path, monkeypatch) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'gateway-workers.sqlite3'}")
    init_db(factory)

    class Service:
        events = None
        pipeline = object()

    monkeypatch.setattr(telegram_workers, "get_gateway_service", lambda: Service())
    monkeypatch.setattr(telegram_workers, "get_session_factory", lambda: factory)

    runtime = telegram_workers.build_background_runtime(bot=object())

    assert [adapter.name for adapter in runtime.adapters] == ["reminders", "actions", "delivery_outbox"]
    assert isinstance(runtime.lease_store, SqlAutomationLeaseStore)


def test_first_runtime_tick_recovers_action_stuck_before_lease_table(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'bootstrap-recovery.sqlite3'}")
    init_db(factory)
    user = UserStore(factory).get_or_create(9001)
    actions = SqlActionQueueStore(factory)
    action = actions.enqueue(user_id=user.id, action_type=ActionType.IDEA_SAVE, payload={"text": "recover"})
    assert actions.claim_next().id == action.id
    delivered = []

    async def execute(_action):
        return "recovered"

    async def deliver(_action, text):
        delivered.append(text)

    runtime = AutomationRuntime(
        [ActionWorkerAdapter(actions, execute, deliver)],
        SqlAutomationLeaseStore(factory),
        owner_id="bootstrap-worker",
    )

    asyncio.run(runtime.tick())

    saved = next(item for item in actions.list_for_user(user.id) if item.id == action.id)
    assert saved.status == ActionStatus.SUCCEEDED
    assert saved.attempts == 2
    assert delivered == [f"Action #{action.id}: recovered"]
