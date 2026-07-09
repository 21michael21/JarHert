from __future__ import annotations

import re

from assistant.types import HermesResponse


class HermesClientError(RuntimeError):
    """Raised when a provider cannot return a usable assistant response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.latency_ms = latency_ms


def normalize_hermes_response(payload: dict, *, latency_ms: int) -> HermesResponse:
    text = extract_provider_text(payload)
    if not text.strip():
        raise HermesClientError("Hermes returned an empty response")

    return HermesResponse(
        text=text.strip(),
        provider=str(payload.get("provider") or payload.get("provider_name") or "hermes"),
        model=str(payload.get("model") or payload.get("model_name") or "unknown"),
        latency_ms=latency_ms,
        fallback_count=_safe_int(payload.get("fallback_count"), default=0),
        fallback_reason=_safe_optional_str(payload.get("fallback_reason")),
        diagnostics=_extract_diagnostics(payload),
    )


def extract_provider_text(payload: dict) -> str:
    for key in ("text", "response", "answer", "content", "output"):
        value = payload.get(key)
        if isinstance(value, str):
            return value

    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(message, str):
        return message

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            choice_message = first.get("message")
            if isinstance(choice_message, dict) and isinstance(choice_message.get("content"), str):
                return choice_message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]

    return ""


def extract_openai_responses_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    chunks: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def looks_like_provider_error(text: str) -> bool:
    lowered = text.strip().lower()
    if re.match(r"^http [45]\d\d\b", lowered):
        return True
    return "user not found" in lowered or "invalid api key" in lowered


def _safe_int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _extract_diagnostics(payload: dict) -> dict[str, str]:
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return {}
    return {str(key): str(value) for key, value in diagnostics.items()}
