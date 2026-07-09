from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from io import BytesIO

from assistant.transcription import OpenAITranscriber, TranscriptionError
from assistant.types import AssistantReply, Intent
from backend.stores import SqlDeliveryOutboxStore
from gateway_bot.blocking_executor import BlockingCallTimeout, get_shared_executor
from gateway_bot.deferred_work import DeferredWork
from gateway_bot.main import get_gateway_service, get_session_factory, settings
from gateway_bot.telegram_callbacks import buttons_to_payload, handle_callback_data


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
    blocking_executor = get_shared_executor(
        max_concurrency=settings.telegram_blocking_max_concurrency,
        timeout_seconds=settings.telegram_blocking_timeout_seconds,
    )
    deferred_work = DeferredWork(fast_ack_seconds=settings.telegram_fast_ack_seconds)
    dp = Dispatcher()

    async def submit_reply(
        *,
        user_id: int,
        chat_id: int,
        root_key: str,
        work,
        accepted_text: str,
    ) -> None:
        def on_result(reply: AssistantReply, delayed: bool) -> None:
            if reply.suppress_delivery:
                return
            _enqueue_reply(
                outbox,
                user_id,
                chat_id,
                reply.text,
                trace_id=reply.trace_id,
                buttons=buttons_to_payload(reply.buttons),
                idempotency_key=f"{root_key}:reply",
            )

        def on_ack() -> None:
            _enqueue_reply(
                outbox,
                user_id,
                chat_id,
                accepted_text,
                idempotency_key=f"{root_key}:accepted",
            )

        def on_error(error: Exception, delayed: bool) -> None:
            logger.warning("Telegram request failed after%s fast acknowledgement: %s", "" if delayed else " no", error)
            _enqueue_reply(
                outbox,
                user_id,
                chat_id,
                _request_error_text(error),
                idempotency_key=f"{root_key}:{'error' if delayed else 'reply'}",
            )

        await deferred_work.submit(
            work,
            on_result=on_result,
            on_ack=on_ack,
            on_error=on_error,
        )

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user is None:
            await message.answer(START_TEXT)
            return
        _enqueue_reply(
            outbox,
            message.from_user.id,
            message.chat.id,
            START_TEXT,
            idempotency_key=f"{_telegram_message_key(message)}:reply",
        )

    @dp.message(F.text)
    async def handle_text(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Не вижу Telegram user id, поэтому не могу обработать сообщение.")
            return
        root_key = _telegram_message_key(message)
        await submit_reply(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            root_key=root_key,
            work=blocking_executor.run_blocking(
                message.from_user.id,
                service.handle_text,
                message.from_user.id,
                message.text or "",
                idempotency_key=root_key,
            ),
            accepted_text="Принял, обрабатываю. Итог пришлю отдельным сообщением.",
        )

    @dp.message(F.voice)
    async def handle_voice(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Не вижу Telegram user id, поэтому не могу обработать голосовое.")
            return
        if not service.is_allowed(message.from_user.id):
            _enqueue_reply(
                outbox,
                message.from_user.id,
                message.chat.id,
                "Этот бот пока закрыт. Попроси владельца добавить твой Telegram ID в allowlist.",
                idempotency_key=f"{_telegram_message_key(message)}:reply",
            )
            return
        if message.voice is None:
            _enqueue_reply(
                outbox,
                message.from_user.id,
                message.chat.id,
                "Не вижу голосовой файл.",
                idempotency_key=f"{_telegram_message_key(message)}:reply",
            )
            return
        if message.voice.file_size and message.voice.file_size > settings.voice_max_bytes:
            _enqueue_reply(
                outbox,
                message.from_user.id,
                message.chat.id,
                "Голосовое слишком большое. Пришли короче или текстом.",
                idempotency_key=f"{_telegram_message_key(message)}:reply",
            )
            return

        root_key = _telegram_message_key(message)
        await submit_reply(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            root_key=root_key,
            work=_process_voice(
                message=message,
                service=service,
                transcriber=transcriber,
                blocking_executor=blocking_executor,
                root_key=root_key,
            ),
            accepted_text="Принял голосовое, расшифровываю. Итог пришлю отдельным сообщением.",
        )

    @dp.callback_query(F.data.startswith("ai:"))
    async def handle_ai_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            await callback.answer("Не вижу Telegram user id.", show_alert=True)
            return
        await callback.answer()
        if callback.message is not None:
            await submit_reply(
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                root_key=f"telegram:callback:{callback.id}",
                work=blocking_executor.run_blocking(
                    callback.from_user.id,
                    handle_callback_data,
                    service,
                    callback.from_user.id,
                    callback.data or "",
                ),
                accepted_text="Принял, выполняю подтверждённое действие.",
            )

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
            idempotency_key=f"{_telegram_message_key(message)}:reply",
        )

    return dp


def _enqueue_reply(
    outbox: SqlDeliveryOutboxStore,
    tg_user_id: int,
    chat_id: int,
    text: str,
    trace_id: str = "",
    buttons: list[list[dict[str, str]]] | None = None,
    idempotency_key: str | None = None,
) -> None:
    service = get_gateway_service()
    db_user = service.users.get_or_create(tg_user_id) if service.users is not None else None
    outbox.enqueue(
        user_id=db_user.id if db_user is not None else tg_user_id,
        chat_id=chat_id,
        text=text,
        trace_id=trace_id,
        buttons=buttons,
        idempotency_key=idempotency_key,
    )


def _telegram_message_key(message: Message) -> str:
    return f"telegram:{message.chat.id}:{message.message_id}"


async def _process_voice(
    *,
    message: Message,
    service,
    transcriber: OpenAITranscriber,
    blocking_executor,
    root_key: str,
) -> AssistantReply:
    assert message.from_user is not None
    assert message.voice is not None
    user_id = message.from_user.id

    async def operation() -> AssistantReply:
        audio = BytesIO()
        try:
            await asyncio.wait_for(
                message.bot.download(message.voice.file_id, destination=audio),
                timeout=settings.telegram_blocking_timeout_seconds,
            )
        except Exception:
            logger.exception("Voice download failed")
            return AssistantReply(
                text="Не смог скачать голосовое из Telegram. Попробуй ещё раз.",
                intent=Intent.UNKNOWN,
            )
        try:
            text = await blocking_executor.run_blocking_unlocked(
                user_id,
                transcriber.transcribe,
                audio.getvalue(),
                filename="voice.oga",
                mime_type=message.voice.mime_type or "audio/ogg",
            )
        except (BlockingCallTimeout, TranscriptionError):
            logger.exception("Voice transcription failed")
            return AssistantReply(
                text="Не смог расшифровать голосовое. Пришли текстом или попробуй ещё раз.",
                intent=Intent.UNKNOWN,
            )
        reply = await blocking_executor.run_blocking_unlocked(
            user_id,
            service.handle_text,
            user_id,
            text,
            idempotency_key=root_key,
        )
        return replace(reply, text=f"Расшифровал: {text}\n\n{reply.text}")

    return await blocking_executor.run_serialized(user_id, operation)


def _request_error_text(error: Exception) -> str:
    if isinstance(error, BlockingCallTimeout):
        return "Действие заняло слишком много времени. Попробуй ещё раз чуть позже."
    return "Не смог обработать сообщение. Попробуй ещё раз."
