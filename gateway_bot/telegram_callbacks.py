from __future__ import annotations


try:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
except ModuleNotFoundError:  # pragma: no cover - exercised by import smoke without deps.
    InlineKeyboardButton = None  # type: ignore[assignment]
    InlineKeyboardMarkup = None  # type: ignore[assignment]


def buttons_to_payload(buttons) -> list[list[dict[str, str]]]:
    return [[{"text": button.text, "callback_data": button.callback_data} for button in row] for row in buttons]


def reply_markup(buttons: list[list[dict[str, str]]] | None):
    if not buttons or InlineKeyboardMarkup is None or InlineKeyboardButton is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button["text"], callback_data=button["callback_data"]) for button in row]
            for row in buttons
        ]
    )


def handle_callback_data(service, tg_user_id: int, data: str):
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "ai" or not parts[2].isdigit():
        return service.handle_text(tg_user_id, "/status")
    item_id = int(parts[2])
    if parts[1] == "confirm_job":
        return service.confirm_job(tg_user_id, item_id)
    if parts[1] == "cancel_job":
        return service.cancel_job(tg_user_id, item_id)
    if parts[1] == "confirm":
        return service.confirm_action(tg_user_id, item_id)
    if parts[1] == "cancel":
        return service.cancel_action(tg_user_id, item_id)
    if parts[1] == "status":
        return service.job_status(tg_user_id, item_id)
    return service.handle_text(tg_user_id, "/status")
