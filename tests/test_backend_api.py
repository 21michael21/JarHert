from __future__ import annotations

from dataclasses import dataclass

from assistant.types import AssistantReply, Intent
from backend import main


@dataclass
class FakeGatewayService:
    def handle_text(self, tg_user_id: int, text: str) -> AssistantReply:
        return AssistantReply(
            text=f"{tg_user_id}:{text}",
            intent=Intent.ASK,
            provider="fake",
            model="fake-model",
        )


def set_service_token(value: str) -> None:
    object.__setattr__(main.settings, "assistant_service_token", value)


def test_assistant_endpoint_requires_service_token() -> None:
    from fastapi.testclient import TestClient

    set_service_token("secret")
    client = TestClient(main.app)

    response = client.post("/api/assistant/telegram-text", json={"tg_user_id": 1001, "text": "/ask привет"})

    assert response.status_code == 401


def test_assistant_endpoint_returns_gateway_reply(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    set_service_token("secret")
    monkeypatch.setattr(main, "get_gateway_service", lambda: FakeGatewayService())
    client = TestClient(main.app)

    response = client.post(
        "/api/assistant/telegram-text",
        headers={"Authorization": "Bearer secret"},
        json={"tg_user_id": 1001, "text": "/ask привет"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "1001:/ask привет",
        "intent": "ask",
        "provider": "fake",
        "model": "fake-model",
        "fallback_count": 0,
        "blocked_reason": None,
    }
