from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from assistant.action_queue import AgentAction
from assistant.action_worker import run_action_worker
from assistant.delivery_outbox import DeliveryMessage, run_delivery_outbox_worker
from assistant.transcription import OpenAITranscriber, TranscriptionError
from assistant.types import UserContext
from backend.stores import SqlActionQueueStore, SqlDeliveryOutboxStore, SqlReminderStore
from gateway_bot.main import get_gateway_service, get_session_factory, settings
from reminders.worker import run_reminder_worker


logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandStart
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
except ModuleNotFoundError:  # pragma: no cover - exercised by import smoke without deps.
    Bot = None  # type: ignore[assignment]
    Dispatcher = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]
    CommandStart = None  # type: ignore[assignment]
    CallbackQuery = object  # type: ignore[assignment,misc]
    InlineKeyboardButton = None  # type: ignore[assignment]
    InlineKeyboardMarkup = None  # type: ignore[assignment]
    Message = object  # type: ignore[assignment,misc]


START_TEXT = "\n".join(
    [
        "AI Brooch: отвечаю, записываю идеи и ставлю напоминания.",
        "Пиши текстом или голосом: /ask, /idea, /remember, /remind через 2 часа текст.",
        "Опасные действия с сервером, файлами и ключами я не выполняю.",
    ]
)


def _ensure_aiogram() -> None:
    if Bot is None or Dispatcher is None or F is None or CommandStart is None:
        raise RuntimeError("aiogram is not installed. Run: .venv/bin/pip install -e '.[dev]'")


def create_dispatcher():
    _ensure_aiogram()
    service = get_gateway_service()
    outbox = SqlDeliveryOutboxStore(get_session_factory())
    transcriber = OpenAITranscriber(
        api_key=settings.openai_api_key,
        model=settings.openai_transcribe_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.hermes_timeout_seconds,
    )
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user is None:
            await message.answer(START_TEXT)
            return
        _enqueue_reply(outbox, message.from_user.id, message.chat.id, START_TEXT)

    @dp.message(F.text)
    async def handle_text(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Не вижу Telegram user id, поэтому не могу обработать сообщение.")
            return
        reply = service.handle_text(message.from_user.id, message.text or "")
        _enqueue_reply(
            outbox,
            message.from_user.id,
            message.chat.id,
            reply.text,
            trace_id=reply.trace_id,
            buttons=_buttons_to_payload(reply.buttons),
        )

    @dp.message(F.voice)
    async def handle_voice(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Не вижу Telegram user id, поэтому не могу обработать голосовое.")
            return
        if message.voice is None:
            _enqueue_reply(outbox, message.from_user.id, message.chat.id, "Не вижу голосовой файл.")
            return
        if message.voice.file_size and message.voice.file_size > settings.voice_max_bytes:
            _enqueue_reply(
                outbox,
                message.from_user.id,
                message.chat.id,
                "Голосовое слишком большое. Пришли короче или текстом.",
            )
            return

        audio = BytesIO()
        try:
            await message.bot.download(message.voice.file_id, destination=audio)
            text = transcriber.transcribe(
                audio.getvalue(),
                filename="voice.oga",
                mime_type=message.voice.mime_type or "audio/ogg",
            )
        except TranscriptionError:
            logger.exception("Voice transcription failed")
            _enqueue_reply(
                outbox,
                message.from_user.id,
                message.chat.id,
                "Не смог расшифровать голосовое. Пришли текстом или попробуй ещё раз.",
            )
            return
        except Exception:
            logger.exception("Voice download failed")
            _enqueue_reply(
                outbox,
                message.from_user.id,
                message.chat.id,
                "Не смог скачать голосовое из Telegram. Попробуй ещё раз.",
            )
            return

        reply = service.handle_text(message.from_user.id, text)
        _enqueue_reply(
            outbox,
            message.from_user.id,
            message.chat.id,
            f"Расшифровал: {text}\n\n{reply.text}",
            trace_id=reply.trace_id,
            buttons=_buttons_to_payload(reply.buttons),
        )

    @dp.callback_query(F.data.startswith("ai:"))
    async def handle_ai_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            await callback.answer("Не вижу Telegram user id.", show_alert=True)
            return
        reply = _handle_callback_data(service, callback.from_user.id, callback.data or "")
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(reply.text, reply_markup=_reply_markup(_buttons_to_payload(reply.buttons)))

    @dp.message()
    async def unsupported(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Я принимаю текст и голосовые. Файлы оставим читалке.")
            return
        _enqueue_reply(
            outbox,
            message.from_user.id,
            message.chat.id,
            "Я принимаю текст и голосовые. Файлы оставим читалке.",
        )

    return dp


async def start_background_workers(bot: Bot) -> None:
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
        await bot.send_message(message.chat_id, message.text, reply_markup=_reply_markup(message.buttons))

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


def _enqueue_reply(
    outbox: SqlDeliveryOutboxStore,
    tg_user_id: int,
    chat_id: int,
    text: str,
    trace_id: str = "",
    buttons: list[list[dict[str, str]]] | None = None,
) -> None:
    service = get_gateway_service()
    db_user = service.users.get_or_create(tg_user_id) if service.users is not None else None
    outbox.enqueue(
        user_id=db_user.id if db_user is not None else tg_user_id,
        chat_id=chat_id,
        text=text,
        trace_id=trace_id,
        buttons=buttons,
    )


def _buttons_to_payload(buttons) -> list[list[dict[str, str]]]:
    return [[{"text": button.text, "callback_data": button.callback_data} for button in row] for row in buttons]


def _reply_markup(buttons: list[list[dict[str, str]]] | None):
    if not buttons or InlineKeyboardMarkup is None or InlineKeyboardButton is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button["text"], callback_data=button["callback_data"]) for button in row]
            for row in buttons
        ]
    )


def _handle_callback_data(service, tg_user_id: int, data: str):
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "ai" or not parts[2].isdigit():
        return service.handle_text(tg_user_id, "/status")
    item_id = int(parts[2])
    if parts[1] == "confirm":
        return service.confirm_action(tg_user_id, item_id)
    if parts[1] == "cancel":
        return service.cancel_action(tg_user_id, item_id)
    if parts[1] == "status":
        return service.job_status(tg_user_id, item_id)
    return service.handle_text(tg_user_id, "/status")


def _tg_user_id_for_internal_user(user_id: int) -> int | None:
    from sqlalchemy import select
    from backend.models import User

    with get_session_factory()() as db:
        return db.scalar(select(User.tg_user_id).where(User.id == user_id))


async def run_polling() -> None:
    _ensure_aiogram()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required to run Telegram polling")

    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=settings.bot_token)
    dp = create_dispatcher()
    await start_background_workers(bot)
    logger.info("Starting Telegram AI Brooch polling in %s mode", settings.hermes_mode)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_polling())
