from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urljoin

from assistant.provider_diagnostics import (
    HermesClientError,
    extract_openai_responses_text,
    extract_provider_text,
    looks_like_provider_error,
    normalize_hermes_response,
)
from assistant.types import HermesRequest, HermesResponse


class HermesClient(Protocol):
    def ask(self, request: HermesRequest) -> HermesResponse:
        """Send a request to a provider and return a normalized response."""


class FakeHermesClient:
    def __init__(self, responses: list[HermesResponse | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[HermesRequest] = []

    def ask(self, request: HermesRequest) -> HermesResponse:
        self.requests.append(request)
        started = time.perf_counter()
        if self.responses:
            next_response = self.responses.pop(0)
            if isinstance(next_response, Exception):
                raise next_response
            return next_response

        latency_ms = int((time.perf_counter() - started) * 1000)
        return HermesResponse(
            text=f"Принял. Короткий ответ по запросу: {request.prompt}",
            provider="fake",
            model="fake-hermes",
            latency_ms=latency_ms,
        )


class HermesHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        path: str = "/api/chat",
        token: str = "",
        timeout_seconds: float = 25,
    ) -> None:
        self.url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        self.token = token
        self.timeout_seconds = timeout_seconds

    def ask(self, request: HermesRequest) -> HermesResponse:
        started = time.perf_counter()
        payload = {
            "message": request.prompt,
            "prompt": request.prompt,
            "session": f"telegram-ai-brooch-user-{request.user.user_id}",
            "metadata": {
                "intent": request.intent.value,
                "context": request.context,
                "telegram_user_hash": str(request.user.user_id),
            },
        }
        if request.system_prompt:
            payload["system_prompt"] = request.system_prompt
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "telegram-ai-brooch/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Hermes-Session-Token"] = self.token

        http_request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise HermesClientError(f"Hermes HTTP error: {error.code}", status_code=error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise HermesClientError("Hermes HTTP request failed") from error

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as error:
            raise HermesClientError("Hermes returned non-JSON HTTP response") from error
        if not isinstance(data, dict):
            raise HermesClientError("Hermes returned unsupported HTTP response")
        return normalize_hermes_response(data, latency_ms=latency_ms)


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5-nano",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 25,
        max_output_tokens: int = 600,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key must not be empty")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/responses"
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    def ask(self, request: HermesRequest) -> HermesResponse:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "input": request.prompt,
            "max_output_tokens": min(self.max_output_tokens, request.max_output_tokens or self.max_output_tokens),
            "reasoning": {"effort": "minimal"},
            "text": {"verbosity": "low"},
        }
        if request.system_prompt:
            payload["instructions"] = request.system_prompt
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "telegram-ai-brooch/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise HermesClientError(f"OpenAI HTTP error: {error.code}", status_code=error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise HermesClientError("OpenAI HTTP request failed") from error

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as error:
            raise HermesClientError("OpenAI returned non-JSON response") from error
        text = extract_openai_responses_text(data)
        if not text.strip():
            status = data.get("status", "unknown") if isinstance(data, dict) else "unknown"
            raise HermesClientError(f"OpenAI returned empty response text, status={status}")
        return HermesResponse(
            text=text.strip(),
            provider="openai",
            model=str(data.get("model") or self.model),
            latency_ms=latency_ms,
        )


class OpenAIChatCompletionsClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        provider: str,
        timeout_seconds: float = 20,
        max_output_tokens: int = 500,
        supports_json: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError(f"{provider} API key must not be empty")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.supports_json = supports_json

    def ask(self, request: HermesRequest) -> HermesResponse:
        started = time.perf_counter()
        system_prompt = request.system_prompt or "Отвечай кратко, по-русски, без воды и без упоминания, что ты ИИ."
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": min(self.max_output_tokens, request.max_output_tokens or self.max_output_tokens),
            "temperature": 0.3,
        }
        if self.supports_json and request.context.get("response_format") == "json":
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "telegram-ai-brooch/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise HermesClientError(
                f"{self.provider} HTTP error: {error.code}",
                status_code=error.code,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise HermesClientError(
                f"{self.provider} HTTP request failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
            ) from error

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as error:
            raise HermesClientError(f"{self.provider} returned non-JSON response", latency_ms=latency_ms) from error
        text = extract_provider_text(data)
        if not text.strip():
            raise HermesClientError(f"{self.provider} returned empty response text", latency_ms=latency_ms)
        return HermesResponse(
            text=text.strip(),
            provider=self.provider,
            model=str(data.get("model") or self.model),
            latency_ms=latency_ms,
        )


class HermesCliClient:
    def __init__(
        self,
        command: Sequence[str] | str,
        *,
        timeout_seconds: float = 25,
        provider: str = "hermes-cli",
        model: str = "unknown",
    ) -> None:
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        if not self.command:
            raise ValueError("Hermes CLI command must not be empty")
        self.timeout_seconds = timeout_seconds
        self.provider = provider
        self.model = model

    def ask(self, request: HermesRequest) -> HermesResponse:
        started = time.perf_counter()
        has_prompt_placeholder = any("{prompt}" in part for part in self.command)
        command = [part.replace("{prompt}", request.prompt) for part in self.command]
        environment = None
        if request.system_prompt:
            environment = os.environ.copy()
            environment["HERMES_EPHEMERAL_SYSTEM_PROMPT"] = request.system_prompt
        try:
            result = subprocess.run(
                command,
                input=None if has_prompt_placeholder else request.prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise HermesClientError("Hermes CLI timed out") from error

        if result.returncode != 0:
            raise HermesClientError(f"Hermes CLI exited with status {result.returncode}")

        text = result.stdout.strip()
        if not text:
            raise HermesClientError("Hermes CLI returned empty stdout")
        if looks_like_provider_error(text):
            raise HermesClientError(f"Hermes CLI returned provider error: {text[:120]}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        return HermesResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            latency_ms=latency_ms,
            diagnostics={"stderr": result.stderr.strip()[:500]},
        )
