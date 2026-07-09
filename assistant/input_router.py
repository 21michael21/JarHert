from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


class InputKind(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    FORWARD = "forward"
    LINK = "link"
    FILE = "file"


@dataclass(frozen=True)
class UnifiedInput:
    kind: InputKind
    text: str = ""
    caption: str = ""
    filename: str = ""
    mime_type: str = ""
    forwarded_from: str = ""
    urls: tuple[str, ...] = field(default_factory=tuple)


def normalize_input_text(inbound: UnifiedInput) -> str:
    text = " ".join((inbound.text or inbound.caption or "").strip().split())
    if text:
        return text
    return ""


def input_from_telegram_message(message: Any, *, default_kind: InputKind | None = None) -> UnifiedInput:
    text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
    urls = tuple(URL_RE.findall(text))
    document = getattr(message, "document", None)
    audio = getattr(message, "audio", None)
    video = getattr(message, "video", None)
    photo = getattr(message, "photo", None)
    file_obj = document or audio or video
    filename = str(getattr(file_obj, "file_name", "") or "")
    mime_type = str(getattr(file_obj, "mime_type", "") or "")
    forwarded_from = _forwarded_from(message)

    if default_kind is not None:
        kind = default_kind
    elif file_obj is not None or photo:
        kind = InputKind.FILE
    elif forwarded_from:
        kind = InputKind.FORWARD
    elif urls:
        kind = InputKind.LINK
    else:
        kind = InputKind.TEXT

    return UnifiedInput(
        kind=kind,
        text=text,
        caption=str(getattr(message, "caption", "") or ""),
        filename=filename,
        mime_type=mime_type,
        forwarded_from=forwarded_from,
        urls=urls,
    )


def _forwarded_from(message: Any) -> str:
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        return str(getattr(origin, "sender_user", None) or getattr(origin, "chat", None) or "forward")
    sender = getattr(message, "forward_from", None)
    if sender is not None:
        return str(getattr(sender, "username", None) or getattr(sender, "full_name", None) or getattr(sender, "id", "") or "forward")
    sender_name = getattr(message, "forward_sender_name", None)
    if sender_name:
        return str(sender_name)
    return ""
