from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderCostMode(str, Enum):
    FREE = "free"
    CHEAP = "cheap"
    PAID = "paid"
    LOCAL = "local"


class ProviderKind(str, Enum):
    OPENAI_RESPONSES = "openai_responses"
    OPENAI_CHAT = "openai_chat"
    HERMES_CLI = "hermes_cli"
    HERMES_HTTP = "hermes_http"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model: str
    cost_mode: ProviderCostMode
    timeout_seconds: float
    max_tokens: int
    supports_json: bool
    priority: int
    kind: ProviderKind
    enabled: bool = True
    credential_env: str = ""
    base_url: str = ""
    command_template: str = ""


class ProviderRegistry:
    def __init__(self, providers: list[ProviderSpec]) -> None:
        self._providers = list(providers)

    def all(self) -> list[ProviderSpec]:
        return sorted(self._providers, key=lambda provider: (provider.priority, provider.name))

    def enabled(self) -> list[ProviderSpec]:
        return [provider for provider in self.all() if provider.enabled]

    def get(self, name: str) -> ProviderSpec:
        for provider in self._providers:
            if provider.name == name:
                return provider
        raise KeyError(f"provider not found: {name}")


def build_provider_registry(settings) -> ProviderRegistry:
    providers: list[ProviderSpec] = []

    if getattr(settings, "openrouter_api_key", ""):
        providers.append(
            ProviderSpec(
                name="openrouter_free",
                model=getattr(settings, "openrouter_model", "openrouter/free"),
                cost_mode=ProviderCostMode.FREE,
                timeout_seconds=float(getattr(settings, "openrouter_timeout_seconds", 12.0)),
                max_tokens=int(getattr(settings, "openrouter_max_output_tokens", 500)),
                supports_json=True,
                priority=10,
                kind=ProviderKind.OPENAI_CHAT,
                credential_env="OPENROUTER_API_KEY",
                base_url=getattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
            )
        )

    if getattr(settings, "openai_api_key", ""):
        providers.append(
            ProviderSpec(
                name="openai_cheap",
                model=getattr(settings, "openai_model", "gpt-5-nano"),
                cost_mode=ProviderCostMode.CHEAP,
                timeout_seconds=float(getattr(settings, "hermes_timeout_seconds", 25.0)),
                max_tokens=int(getattr(settings, "openai_max_output_tokens", 600)),
                supports_json=True,
                priority=20,
                kind=ProviderKind.OPENAI_RESPONSES,
                credential_env="OPENAI_API_KEY",
                base_url=getattr(settings, "openai_base_url", "https://api.openai.com/v1"),
            )
        )

    for index, model in enumerate(getattr(settings, "hermes_cli_models", []) or [], start=1):
        safe_name = _safe_provider_name(model)
        providers.append(
            ProviderSpec(
                name=f"hermes_cli_{safe_name}",
                model=model,
                cost_mode=ProviderCostMode.LOCAL,
                timeout_seconds=float(getattr(settings, "hermes_timeout_seconds", 25.0)),
                max_tokens=int(getattr(settings, "openai_max_output_tokens", 600)),
                supports_json=False,
                priority=30 + index,
                kind=ProviderKind.HERMES_CLI,
                command_template=getattr(settings, "hermes_cli_command_template", ""),
            )
        )

    if getattr(settings, "ai_allow_paid_fallback", False):
        for index, model in enumerate(getattr(settings, "hermes_paid_fallback_models", []) or [], start=1):
            safe_name = _safe_provider_name(model)
            providers.append(
                ProviderSpec(
                    name=f"hermes_cli_paid_{safe_name}",
                    model=model,
                    cost_mode=ProviderCostMode.PAID,
                    timeout_seconds=float(getattr(settings, "hermes_timeout_seconds", 25.0)),
                    max_tokens=int(getattr(settings, "openai_max_output_tokens", 600)),
                    supports_json=False,
                    priority=80 + index,
                    kind=ProviderKind.HERMES_CLI,
                    command_template=getattr(settings, "hermes_cli_command_template", ""),
                )
            )

    if getattr(settings, "groq_api_key", ""):
        providers.append(
            ProviderSpec(
                name="groq",
                model=getattr(settings, "groq_model", "llama-3.1-8b-instant"),
                cost_mode=ProviderCostMode.FREE,
                timeout_seconds=float(getattr(settings, "groq_timeout_seconds", 10.0)),
                max_tokens=int(getattr(settings, "groq_max_output_tokens", 500)),
                supports_json=True,
                priority=40,
                kind=ProviderKind.OPENAI_CHAT,
                credential_env="GROQ_API_KEY",
                base_url=getattr(settings, "groq_base_url", "https://api.groq.com/openai/v1"),
            )
        )

    if getattr(settings, "hf_api_key", ""):
        providers.append(
            ProviderSpec(
                name="huggingface",
                model=getattr(settings, "hf_model", "meta-llama/Llama-3.1-8B-Instruct"),
                cost_mode=ProviderCostMode.FREE,
                timeout_seconds=float(getattr(settings, "hf_timeout_seconds", 15.0)),
                max_tokens=int(getattr(settings, "hf_max_output_tokens", 500)),
                supports_json=False,
                priority=50,
                kind=ProviderKind.OPENAI_CHAT,
                credential_env="HF_API_KEY",
                base_url=getattr(settings, "hf_base_url", "https://router.huggingface.co/v1"),
            )
        )

    return ProviderRegistry(providers)


def _safe_provider_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")
