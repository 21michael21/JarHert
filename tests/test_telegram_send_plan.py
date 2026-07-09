from __future__ import annotations

from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.contact_book import InMemoryContactBookStore
from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext


def user() -> UserContext:
    return UserContext(user_id=1, tg_user_id=1001)


def make_pipeline():
    contacts = InMemoryContactBookStore()
    contacts.upsert(user_id=1, name="Илья", aliases=["илье", "илюха"], tg_user_id=2002, chat_id=3003)
    queue = InMemoryActionQueueStore()
    outbox = InMemoryDeliveryOutboxStore()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        action_queue=queue,
        contact_book=contacts,
        delivery_outbox=outbox,
    )
    return pipeline, queue, outbox


def test_prepare_message_to_contact_requires_one_job_confirmation_and_preview() -> None:
    pipeline, queue, _outbox = make_pipeline()

    reply = pipeline.handle_text(user(), "подготовь сообщение Илье: привет, проверим деплой?")
    actions = queue.list_for_user(user().user_id)

    assert "Нужно одно подтверждение" in reply.text
    assert "Илье" in reply.text
    assert "привет, проверим деплой?" in reply.text
    assert reply.buttons[0][0].callback_data == "ai:confirm_job:1"
    assert len(actions) == 1
    assert actions[0].status == ActionStatus.NEEDS_CONFIRMATION
    assert actions[0].payload["recipient"] == "Илье"
    assert actions[0].payload["text"] == "привет, проверим деплой?"


def test_contact_commands_create_and_list_contact() -> None:
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(), plain_text_ai_enabled=True)

    created = pipeline.handle_text(
        user(),
        "/contact add Илья | alias=илье,илюха | tg_user_id=2002 | chat_id=3003",
    )
    listed = pipeline.handle_text(user(), "/contacts")

    assert "Сохранил контакт #1" in created.text
    assert "Илья" in listed.text
    assert "илье" in listed.text
    assert "3003" in listed.text


def test_confirmed_contact_message_goes_to_delivery_outbox() -> None:
    pipeline, queue, outbox = make_pipeline()
    pipeline.handle_text(user(), "подготовь сообщение Илье: привет")
    assert queue.confirm_job_for_user(user().user_id, 1)

    action = queue.claim_next()
    result = pipeline.execute_queued_action_result(user(), action)
    queue.mark_succeeded(action.id, result_meta=result.meta, result_text=result.message)
    message = outbox.list_recent()[0]

    assert message.chat_id == 3003
    assert message.text == "привет"
    assert result.meta["telegram_chat_id"] == "3003"
    assert result.meta["contact_id"] == "1"


def test_multi_action_plan_gets_single_confirmation_button() -> None:
    pipeline, queue, _outbox = make_pipeline()

    reply = pipeline.handle_text(user(), "напомни написать Илье завтра и подготовь сообщение Илье: привет")
    actions = sorted(queue.list_for_user(user().user_id), key=lambda item: item.id)

    assert "Нужно одно подтверждение" in reply.text
    assert len(reply.buttons[0]) == 2
    assert reply.buttons[0][0].callback_data == "ai:confirm_job:1"
    assert len(actions) == 2
    assert all(action.status == ActionStatus.NEEDS_CONFIRMATION for action in actions)
    assert actions[1].depends_on_action_id == actions[0].id


def test_send_tomorrow_uses_previous_prepared_message_context() -> None:
    pipeline, queue, _outbox = make_pipeline()

    pipeline.handle_text(user(), "подготовь сообщение Илье: привет")
    reply = pipeline.handle_text(user(), "отправь завтра")
    actions = sorted(queue.list_for_user(user().user_id), key=lambda item: item.id)

    assert "Нужно одно подтверждение" in reply.text
    assert actions[-1].payload["recipient"] == "Илье"
    assert actions[-1].payload["text"] == "привет"
    assert actions[-1].payload["send_at"] == "завтра"
