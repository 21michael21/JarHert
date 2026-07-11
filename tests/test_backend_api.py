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


def test_readiness_checks_schema_without_exposing_configuration(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    checked = []
    monkeypatch.setattr(main, "require_current_schema", lambda database_url: checked.append(database_url))
    client = TestClient(main.app)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"] == {"schema": "ok"}
    assert "database_url" not in str(response.json()).lower()
    assert checked


def test_coding_queue_endpoints_require_token_and_preserve_worker_lease(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    class Store:
        def __init__(self) -> None:
            self.calls = []

        def enqueue(self, **payload):
            self.calls.append(("enqueue", payload))
            return type("Job", (), {**payload, "id": 7, "status": "queued", "worker_id": None,
                                     "lease_until": None, "result_text": None, "last_error": None,
                                     "created_at": None, "updated_at": None})()

        def claim_next(self, **payload):
            self.calls.append(("claim", payload))
            return type("Job", (), {"id": 7, "user_id": 1, "mode": "coding", "prompt": "fix",
                                     "repository_url": "https://github.com/example/repo", "source_urls": [],
                                     "idempotency_key": "k", "status": "running", "worker_id": payload["worker_id"],
                                     "lease_until": None, "result_text": None, "last_error": None,
                                     "created_at": None, "updated_at": None})()

        def complete(self, job_id, **payload):
            self.calls.append(("complete", {"job_id": job_id, **payload}))
            return self.claim_next(worker_id=payload["worker_id"])

    store = Store()
    set_service_token("secret")
    monkeypatch.setattr(main, "get_coding_job_store", lambda: store)
    monkeypatch.setattr(main, "get_or_create_user_id", lambda _tg_user_id: 1)
    client = TestClient(main.app)

    assert client.post("/api/coding/jobs/claim", json={"worker_id": "mac-a"}).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    created = client.post(
        "/api/coding/jobs",
        headers=headers,
        json={
            "tg_user_id": 1001,
            "mode": "coding",
            "prompt": "fix",
            "repository_url": "https://github.com/example/repo",
            "idempotency_key": "k",
        },
    )
    claimed = client.post("/api/coding/jobs/claim", headers=headers, json={"worker_id": "mac-a"})
    completed = client.post(
        "/api/coding/jobs/7/complete",
        headers=headers,
        json={"worker_id": "mac-a", "result_text": "done"},
    )

    assert created.status_code == claimed.status_code == completed.status_code == 200
    assert claimed.json()["worker_id"] == "mac-a"
    assert [name for name, _payload in store.calls if name != "claim"] == ["enqueue", "complete"]
