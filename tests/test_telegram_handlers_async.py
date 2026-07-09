from __future__ import annotations

import asyncio
from types import SimpleNamespace

from assistant.types import AssistantReply, Intent
from gateway_bot.blocking_executor import BoundedUserExecutor
from gateway_bot.telegram_handlers import _process_voice


def test_voice_pipeline_downloads_then_transcribes_and_handles_in_one_user_queue() -> None:
    calls: list[str] = []

    class Bot:
        async def download(self, _file_id, *, destination) -> None:
            calls.append("download")
            destination.write(b"voice-bytes")

    class Transcriber:
        def transcribe(self, audio: bytes, **_kwargs) -> str:
            assert audio == b"voice-bytes"
            calls.append("transcribe")
            return "поставь напоминание"

    class Service:
        def handle_text(self, user_id, text, *, idempotency_key):
            calls.append("handle_text")
            assert (user_id, text, idempotency_key) == (1001, "поставь напоминание", "telegram:1:2")
            return AssistantReply(text="Поставил.", intent=Intent.REMIND)

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1001),
        voice=SimpleNamespace(file_id="voice-file", mime_type="audio/ogg"),
        bot=Bot(),
    )
    executor = BoundedUserExecutor(max_concurrency=2, timeout_seconds=1)

    try:
        reply = asyncio.run(
            _process_voice(
                message=message,
                service=Service(),
                transcriber=Transcriber(),
                blocking_executor=executor,
                root_key="telegram:1:2",
            )
        )
    finally:
        executor.close()

    assert calls == ["download", "transcribe", "handle_text"]
    assert reply.text == "Расшифровал: поставь напоминание\n\nПоставил."
