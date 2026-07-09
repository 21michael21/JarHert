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
    from aiogram.types import Message
except ModuleNotFoundError:  # pragma: no cover - exercised by import smoke without deps.
    Bot = None  # type: ignore[assignment]
    Dispatcher = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]
    CommandStart = None  # type: ignore[assignment]
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
        _enqueue_reply(outbox, message.from_user.id, message.chat.id, reply.text)

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
        _enqueue_reply(outbox, message.from_user.id, message.chat.id, f"Расшифровал: {text}\n\n{reply.text}")

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
        )
        if service.events is not None:
            service.events.log(reminder.user_id, "assistant_reminder_queued", {"reminder_id": reminder.id})

    async def send_delivery(message: DeliveryMessage) -> None:
        await bot.send_message(message.chat_id, message.text)
        if service.events is not None:
            service.events.log(message.user_id, "assistant_delivery_sent", {"delivery_id": message.id})

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
        outbox.enqueue(user_id=action.user_id, chat_id=tg_user_id, text=text)
        if service.events is not None:
            service.events.log(action.user_id, "assistant_action_result_queued", {"action_id": action.id})

    asyncio.create_task(run_reminder_worker(reminder_store, enqueue_reminder))
    asyncio.create_task(run_action_worker(action_queue, execute_action, deliver_action_result))
    asyncio.create_task(run_delivery_outbox_worker(outbox, send_delivery))


def _enqueue_reply(
    outbox: SqlDeliveryOutboxStore,
    tg_user_id: int,
    chat_id: int,
    text: str,
) -> None:
    service = get_gateway_service()
    db_user = service.users.get_or_create(tg_user_id) if service.users is not None else None
    outbox.enqueue(
        user_id=db_user.id if db_user is not None else tg_user_id,
        chat_id=chat_id,
        text=text,
    )


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
