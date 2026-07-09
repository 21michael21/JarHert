from __future__ import annotations

from types import SimpleNamespace

from assistant.input_router import InputKind, UnifiedInput, input_from_telegram_message, normalize_input_text


def test_normalizes_plain_text_input() -> None:
    inbound = UnifiedInput(kind=InputKind.TEXT, text="  сохрани мысль проверить OAuth  ")

    assert normalize_input_text(inbound) == "сохрани мысль проверить OAuth"


def test_normalizes_voice_transcript_as_text() -> None:
    inbound = UnifiedInput(kind=InputKind.VOICE, text="напомни завтра проверить календарь")

    assert normalize_input_text(inbound) == "напомни завтра проверить календарь"


def test_detects_forwarded_text_without_losing_content() -> None:
    message = SimpleNamespace(
        text="идея сделать digest",
        caption=None,
        forward_origin=object(),
        forward_from=None,
        forward_sender_name=None,
        document=None,
        photo=None,
        audio=None,
        video=None,
    )

    inbound = input_from_telegram_message(message)

    assert inbound.kind == InputKind.FORWARD
    assert normalize_input_text(inbound) == "идея сделать digest"


def test_detects_link_message() -> None:
    inbound = input_from_telegram_message(
        SimpleNamespace(
            text="сохрани https://example.com/oauth как важное",
            caption=None,
            forward_origin=None,
            forward_from=None,
            forward_sender_name=None,
            document=None,
            photo=None,
            audio=None,
            video=None,
        )
    )

    assert inbound.kind == InputKind.LINK
    assert inbound.urls == ("https://example.com/oauth",)


def test_file_without_instruction_becomes_clarification_text() -> None:
    message = SimpleNamespace(
        text=None,
        caption=None,
        forward_origin=None,
        forward_from=None,
        forward_sender_name=None,
        document=SimpleNamespace(file_name="brief.pdf", mime_type="application/pdf"),
        photo=None,
        audio=None,
        video=None,
    )

    inbound = input_from_telegram_message(message)

    assert inbound.kind == InputKind.FILE
    assert normalize_input_text(inbound) == ""
    assert inbound.filename == "brief.pdf"
