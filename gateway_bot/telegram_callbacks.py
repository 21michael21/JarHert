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


def handle_callback_data(service, tg_user_id: int, data: str, *, update_trace_id: str = ""):
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "ai" or not parts[2].isdigit():
        reply = service.handle_text(tg_user_id, "/status", trace_id=update_trace_id)
        return _log_callback_event(service, tg_user_id, reply, update_trace_id)
    item_id = int(parts[2])
    if parts[1] == "confirm_job":
        reply = service.confirm_job(tg_user_id, item_id)
    elif parts[1] == "cancel_job":
        reply = service.cancel_job(tg_user_id, item_id)
    elif parts[1] == "pause_job":
        reply = service.pause_job(tg_user_id, item_id)
    elif parts[1] == "resume_job":
        reply = service.resume_job(tg_user_id, item_id)
    elif parts[1] == "confirm":
        reply = service.confirm_action(tg_user_id, item_id)
    elif parts[1] == "cancel":
        reply = service.cancel_action(tg_user_id, item_id)
    elif parts[1] == "status":
        reply = service.job_status(tg_user_id, item_id)
    elif parts[1] == "feedback_ok":
        reply = service.approve_training_reply(tg_user_id, item_id)
    elif parts[1] == "feedback_shorter":
        reply = service.shorten_training_reply(tg_user_id, item_id, trace_id=update_trace_id)
    elif parts[1] == "feedback_edit":
        reply = service.edit_training_reply(tg_user_id, item_id)
    else:
        reply = service.handle_text(tg_user_id, "/status", trace_id=update_trace_id)
    return _log_callback_event(service, tg_user_id, reply, update_trace_id)


def _log_callback_event(service, tg_user_id: int, reply, update_trace_id: str):
    if getattr(service, "events", None) is not None:
        user = service._user_context(tg_user_id)
        if user is not None:
            service.events.log(
                user.user_id,
                "telegram_callback_received",
                {"source": "telegram"},
                trace_id=reply.trace_id or update_trace_id,
            )
    return reply
