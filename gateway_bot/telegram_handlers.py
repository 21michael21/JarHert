from __future__ import annotations

import logging
from io import BytesIO

from assistant.transcription import OpenAITranscriber, TranscriptionError
from backend.stores import SqlDeliveryOutboxStore
from gateway_bot.main import get_gateway_service, get_session_factory, settings
from gateway_bot.telegram_callbacks import buttons_to_payload, handle_callback_data, reply_markup


logger = logging.getLogger(__name__)

try:
    from aiogram import Dispatcher, F
    from aiogram.filters import CommandStart
    from aiogram.types import CallbackQuery, Message
except ModuleNotFoundError:  # pragma: no cover - exercised by import smoke without deps.
    Dispatcher = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]
    CommandStart = None  # type: ignore[assignment]
    CallbackQuery = object  # type: ignore[assignment,misc]
    Message = object  # type: ignore[assignment,misc]


START_TEXT = "\n".join(
    [
        "AI Brooch: отвечаю, записываю идеи и ставлю напоминания.",
        "Пиши текстом или голосом: /ask, /idea, /remember, /remind через 2 часа текст.",
        "Опасные действия с сервером, файлами и ключами я не выполняю.",
    ]
)


def ensure_aiogram() -> None:
    if Dispatcher is None or F is None or CommandStart is None:
        raise RuntimeError("aiogram is not installed. Run: .venv/bin/pip install -e '.[dev]'")


def create_dispatcher():
    ensure_aiogram()
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
            buttons=buttons_to_payload(reply.buttons),
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
            buttons=buttons_to_payload(reply.buttons),
        )

    @dp.callback_query(F.data.startswith("ai:"))
    async def handle_ai_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            await callback.answer("Не вижу Telegram user id.", show_alert=True)
            return
        reply = handle_callback_data(service, callback.from_user.id, callback.data or "")
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(reply.text, reply_markup=reply_markup(buttons_to_payload(reply.buttons)))

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
