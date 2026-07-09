from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.hermes_client import FakeHermesClient
from assistant.telegram_trends import parse_trend_decision, run_telegram_trend_once
from assistant.types import HermesResponse
from backend.message_store import CollectedMessage


class FakeMessageStore:
    def __init__(self, messages: list[CollectedMessage]) -> None:
        self.messages = messages
        self.processed: list[int] = []

    def list_unprocessed(self, *, since=None, limit: int = 300):
        return self.messages[:limit]

    def mark_processed(self, message_ids: list[int]) -> int:
        self.processed.extend(message_ids)
        return len(message_ids)


def test_telegram_trend_worker_enqueues_summary_when_triggered() -> None:
    messages = [
        _message(1, "Все обсуждают новый релиз и скрытую фичу."),
        _message(2, "Релиз снова всплыл, похоже там важное изменение."),
    ]
    store = FakeMessageStore(messages)
    outbox = InMemoryDeliveryOutboxStore()
    hermes = FakeHermesClient([HermesResponse('{"triggered": true, "summary": "Тренд: новый релиз обсуждают чаще обычного."}')])

    decision = asyncio.run(
        run_telegram_trend_once(
            store,
            hermes,
            outbox,
            user_id=1,
            chat_id=1001,
        )
    )

    assert decision is not None and decision.triggered
    assert outbox.list_recent()[0].text == "Тренд: новый релиз обсуждают чаще обычного."
    assert store.processed == [1, 2]


def test_telegram_trend_worker_marks_processed_and_stays_silent_when_false() -> None:
    store = FakeMessageStore([_message(1, "обычный шум без тренда")])
    outbox = InMemoryDeliveryOutboxStore()
    hermes = FakeHermesClient([HermesResponse('{"triggered": false, "summary": null}')])

    decision = asyncio.run(
        run_telegram_trend_once(
            store,
            hermes,
            outbox,
            user_id=1,
            chat_id=1001,
        )
    )

    assert decision is not None and not decision.triggered
    assert outbox.list_recent() == []
    assert store.processed == [1]


def test_parse_trend_decision_requires_strict_json_shape() -> None:
    decision = parse_trend_decision('```json\n{"triggered": true, "summary": "Есть сигнал."}\n```')

    assert decision.triggered
    assert decision.summary == "Есть сигнал."


def _message(message_id: int, text: str) -> CollectedMessage:
    return CollectedMessage(
        id=message_id,
        chat_id=-1001,
        chat_title="Тестовый чат",
        sender_id=101,
        sender_name="Sender",
        text=text,
        timestamp=datetime.now(timezone.utc),
        telegram_message_id=message_id,
    )
