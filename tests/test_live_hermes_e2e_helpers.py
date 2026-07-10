from dataclasses import dataclass

from scripts.live_hermes_e2e import approval_button


@dataclass
class Button:
    text: str


@dataclass
class Message:
    message: str
    buttons: list[list[Button]]


def test_approval_button_supports_numbered_telegram_clarify_buttons() -> None:
    message = Message(
        message="1. Выполнить\n2. Отмена",
        buttons=[[Button("1"), Button("2"), Button("✏️ Other (type answer)")]],
    )

    assert approval_button(message, "Выполнить") == "1"
    assert approval_button(message, "Экспортировать") is None
