from __future__ import annotations

import asyncio
import logging

from gateway_bot.main import settings
from gateway_bot.blocking_executor import close_shared_executor
from gateway_bot.telegram_handlers import START_TEXT, create_dispatcher, ensure_aiogram
from gateway_bot.telegram_workers import start_background_workers


logger = logging.getLogger(__name__)

try:
    from aiogram import Bot
except ModuleNotFoundError:  # pragma: no cover - exercised by import smoke without deps.
    Bot = None  # type: ignore[assignment]


def ensure_legacy_gateway_owner(owner: str) -> None:
    if owner.strip().lower() != "legacy":
        raise RuntimeError(
            "Hermes owns Telegram by default. Refusing to start the legacy polling gateway with this bot token. "
            "Set TELEGRAM_GATEWAY_OWNER=legacy only for an intentional legacy-only setup."
        )


async def run_polling() -> None:
    ensure_aiogram()
    if Bot is None:
        raise RuntimeError("aiogram is not installed. Run: .venv/bin/pip install -e '.[dev]'")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required to run Telegram polling")
    ensure_legacy_gateway_owner(settings.telegram_gateway_owner)

    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=settings.bot_token)
    dp = create_dispatcher()
    await start_background_workers(bot)
    logger.info("Starting Telegram AI Brooch polling in %s mode", settings.hermes_mode)
    try:
        await dp.start_polling(bot)
    finally:
        close_shared_executor()


if __name__ == "__main__":
    asyncio.run(run_polling())
