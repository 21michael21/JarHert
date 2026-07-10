from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from assistant.hermes_client import (
    FallbackHermesClient,
    FakeHermesClient,
    HermesCliClient,
    HermesClientError,
    HermesHttpClient,
    OpenAIChatCompletionsClient,
    OpenAIResponsesClient,
    normalize_hermes_response,
)
from assistant.provider_clients import HermesCliClient as SplitHermesCliClient
from assistant.provider_diagnostics import normalize_hermes_response as split_normalize_hermes_response
from assistant.provider_fallback import FallbackHermesClient as SplitFallbackHermesClient
from assistant.types import HermesRequest, HermesResponse, UserContext


def request(*, system_prompt: str = "", max_output_tokens: int | None = None) -> HermesRequest:
    return HermesRequest(
        user=UserContext(user_id=7, tg_user_id=1007),
        prompt="объясни MVP",
        system_prompt=system_prompt,
        max_output_tokens=max_output_tokens,
    )


def test_normalize_openai_style_response() -> None:
    response = normalize_hermes_response(
        {
            "choices": [{"message": {"content": "Готово"}}],
            "provider": "openrouter",
            "model": "free-model",
        },
        latency_ms=12,
    )
    assert response.text == "Готово"
    assert response.provider == "openrouter"
    assert response.model == "free-model"
    assert response.latency_ms == 12


def test_split_provider_modules_keep_compatibility_exports() -> None:
    assert SplitHermesCliClient is HermesCliClient
    assert SplitFallbackHermesClient is FallbackHermesClient
    assert split_normalize_hermes_response is normalize_hermes_response


def test_normalize_rejects_empty_response() -> None:
    with pytest.raises(HermesClientError):
        normalize_hermes_response({"text": ""}, latency_ms=1)


def test_http_client_posts_prompt_and_normalizes_response() -> None:
    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            seen.update(payload)
            body = json.dumps(
                {
                    "text": "Ответ Hermes",
                    "provider": "gemini",
                    "model": "flash",
                    "fallback_count": 1,
                    "fallback_reason": "openrouter_429",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HermesHttpClient(
            base_url=f"http://127.0.0.1:{server.server_port}",
            path="/chat",
            timeout_seconds=2,
        )
        response = client.ask(request(system_prompt="Отвечай кратко."))
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert seen["prompt"] == "объясни MVP"
    assert seen["message"] == "объясни MVP"
    assert seen["system_prompt"] == "Отвечай кратко."
    assert seen["session"] == "telegram-ai-brooch-user-7"
    assert response.text == "Ответ Hermes"
    assert response.provider == "gemini"
    assert response.fallback_count == 1
    assert response.fallback_reason == "openrouter_429"


def test_http_client_rejects_non_200() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(503)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HermesHttpClient(
            base_url=f"http://127.0.0.1:{server.server_port}",
            path="/chat",
            timeout_seconds=2,
        )
        with pytest.raises(HermesClientError):
            client.ask(request())
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_cli_client_uses_stdin_and_stdout() -> None:
    code = "import sys; prompt=sys.stdin.read().strip(); print('CLI:' + prompt)"
    client = HermesCliClient([sys.executable, "-c", code], timeout_seconds=2)
    response = client.ask(request())
    assert response.text == "CLI:объясни MVP"
    assert response.provider == "hermes-cli"


def test_cli_client_replaces_prompt_placeholder_without_shell() -> None:
    code = "import sys; print('CLI:' + sys.argv[1])"
    client = HermesCliClient([sys.executable, "-c", code, "{prompt}"], timeout_seconds=2)
    response = client.ask(request())
    assert response.text == "CLI:объясни MVP"
    assert response.provider == "hermes-cli"


def test_cli_client_passes_style_as_ephemeral_system_prompt() -> None:
    code = "import os; print(os.getenv('HERMES_EPHEMERAL_SYSTEM_PROMPT', 'missing'))"
    client = HermesCliClient([sys.executable, "-c", code], timeout_seconds=2)

    response = client.ask(request(system_prompt="Отвечай прямо."))

    assert response.text == "Отвечай прямо."
    assert os.getenv("HERMES_EPHEMERAL_SYSTEM_PROMPT") is None


def test_cli_client_rejects_nonzero_exit() -> None:
    code = "import sys; sys.stderr.write('bad'); sys.exit(2)"
    client = HermesCliClient([sys.executable, "-c", code], timeout_seconds=2)
    with pytest.raises(HermesClientError):
        client.ask(request())


def test_cli_client_rejects_provider_error_stdout() -> None:
    code = "print('HTTP 400: bad model id')"
    client = HermesCliClient([sys.executable, "-c", code], timeout_seconds=2)
    with pytest.raises(HermesClientError):
        client.ask(request())


def test_fallback_client_uses_next_client_after_error() -> None:
    bad = HermesCliClient([sys.executable, "-c", "print('HTTP 401: User not found.')"], timeout_seconds=2)
    good = HermesCliClient(
        [sys.executable, "-c", "print('ok from fallback')"],
        timeout_seconds=2,
        provider="router",
        model="fallback-model",
    )
    response = FallbackHermesClient([bad, good]).ask(request())

    assert response.text == "ok from fallback"
    assert response.provider == "router"
    assert response.model == "fallback-model"
    assert response.fallback_count == 1


def test_fallback_client_uses_next_client_after_low_quality_answer() -> None:
    bad = FakeHermesClient(
        [
            HermesResponse(
                text="Хорошо, мне нужно ответить по-русски. Сначала подумаю, что сказать.",
                provider="router",
                model="openrouter/free",
            )
        ]
    )
    good = FakeHermesClient([HermesResponse(text="Короткий полезный ответ.", provider="router", model="gemini")])

    response = FallbackHermesClient([bad, good]).ask(request())

    assert response.text == "Короткий полезный ответ."
    assert response.model == "gemini"
    assert response.fallback_count == 1


def test_openai_responses_client_extracts_nested_output_text() -> None:
    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            seen.update(json.loads(self.rfile.read(length)))
            body = json.dumps(
                {
                    "model": "gpt-5-nano-2025-08-07",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "OpenAI ok"}],
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAIResponsesClient(
            api_key="test-key",
            model="gpt-5-nano",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            timeout_seconds=2,
        )
        response = client.ask(request(system_prompt="Сначала дай вывод.", max_output_tokens=120))
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert seen["model"] == "gpt-5-nano"
    assert seen["input"] == "объясни MVP"
    assert seen["instructions"] == "Сначала дай вывод."
    assert seen["max_output_tokens"] == 120
    assert response.text == "OpenAI ok"
    assert response.provider == "openai"
    assert response.model == "gpt-5-nano-2025-08-07"


def test_openai_chat_client_uses_native_system_message() -> None:
    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            seen.update(json.loads(self.rfile.read(length)))
            body = json.dumps(
                {"model": "free-model", "choices": [{"message": {"content": "Короткий ответ"}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenAIChatCompletionsClient(
            api_key="test-key",
            model="free-model",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            provider="test-provider",
        )
        response = client.ask(request(system_prompt="Сначала дай вывод.", max_output_tokens=120))
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert seen["messages"] == [
        {"role": "system", "content": "Сначала дай вывод."},
        {"role": "user", "content": "объясни MVP"},
    ]
    assert seen["max_tokens"] == 120
    assert response.text == "Короткий ответ"
