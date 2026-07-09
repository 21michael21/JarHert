from __future__ import annotations

import asyncio

from assistant.action_queue import AgentAction
from assistant.action_worker import run_action_worker
from assistant.delivery_outbox import DeliveryMessage, run_delivery_outbox_worker
from assistant.types import UserContext
from backend.stores import SqlActionQueueStore, SqlDeliveryOutboxStore, SqlReminderStore
from gateway_bot.main import get_gateway_service, get_session_factory
from gateway_bot.telegram_callbacks import reply_markup
from reminders.worker import run_reminder_worker


async def start_background_workers(bot) -> None:
    service = get_gateway_service()
    action_queue = SqlActionQueueStore(get_session_factory())
    reminder_store = SqlReminderStore(get_session_factory())
    outbox = SqlDeliveryOutboxStore(get_session_factory())

    async def enqueue_reminder(reminder) -> None:
        tg_user_id = _tg_user_id_for_internal_user(reminder.user_id)
        if tg_user_id is None:
            return
        outbox.enqueue(
            user_id=reminder.user_id,
            chat_id=tg_user_id,
            text=f"Напоминание #{reminder.id}: {reminder.text}",
            trace_id=f"reminder-{reminder.id}",
        )
        if service.events is not None:
            service.events.log(
                reminder.user_id,
                "delivery_queued",
                {"reminder_id": reminder.id},
                trace_id=f"reminder-{reminder.id}",
            )

    async def send_delivery(message: DeliveryMessage) -> None:
        await bot.send_message(message.chat_id, message.text, reply_markup=reply_markup(message.buttons))

    async def execute_action(action: AgentAction) -> str:
        tg_user_id = _tg_user_id_for_internal_user(action.user_id)
        if tg_user_id is None:
            raise RuntimeError(f"Telegram user not found for internal user {action.user_id}")
        return service.pipeline.execute_queued_action(
            UserContext(user_id=action.user_id, tg_user_id=tg_user_id),
            action,
        )

    async def deliver_action_result(action: AgentAction, text: str) -> None:
        tg_user_id = _tg_user_id_for_internal_user(action.user_id)
        if tg_user_id is None:
            return
        outbox.enqueue(
            user_id=action.user_id,
            chat_id=tg_user_id,
            text=text,
            trace_id=action.trace_id,
            buttons=[[{"text": "Статус job", "callback_data": f"ai:status:{action.job_id}"}]] if action.job_id else [],
        )
        if service.events is not None:
            service.events.log(
                action.user_id,
                "delivery_queued",
                {"action_id": action.id, "job_id": action.job_id},
                trace_id=action.trace_id,
            )

    def log_action_event(action: AgentAction, event_type: str, meta: dict) -> None:
        if service.events is not None:
            service.events.log(
                action.user_id,
                event_type,
                {"action_id": action.id, "job_id": action.job_id, **meta},
                trace_id=action.trace_id,
            )

    def log_delivery_event(message: DeliveryMessage, event_type: str, meta: dict) -> None:
        if service.events is not None:
            service.events.log(
                message.user_id,
                event_type,
                {"delivery_id": message.id, **meta},
                trace_id=message.trace_id,
            )

    asyncio.create_task(run_reminder_worker(reminder_store, enqueue_reminder))
    asyncio.create_task(
        run_action_worker(action_queue, execute_action, deliver_action_result, event_logger=log_action_event)
    )
    asyncio.create_task(run_delivery_outbox_worker(outbox, send_delivery, event_logger=log_delivery_event))


def _tg_user_id_for_internal_user(user_id: int) -> int | None:
    from sqlalchemy import select

    from backend.models import User

    with get_session_factory()() as db:
        return db.scalar(select(User.tg_user_id).where(User.id == user_id))
