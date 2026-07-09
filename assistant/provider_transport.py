from __future__ import annotations

from assistant.provider_clients import HermesCliClient, HermesClient, HermesHttpClient, OpenAIChatCompletionsClient, OpenAIResponsesClient
from assistant.provider_registry import ProviderKind, ProviderSpec


def build_provider_client(provider: ProviderSpec, settings) -> HermesClient:
    if provider.kind == ProviderKind.OPENAI_RESPONSES:
        return OpenAIResponsesClient(
            api_key=settings.openai_api_key,
            model=provider.model,
            base_url=provider.base_url,
            timeout_seconds=provider.timeout_seconds,
            max_output_tokens=provider.max_tokens,
        )
    if provider.kind == ProviderKind.OPENAI_CHAT:
        return OpenAIChatCompletionsClient(
            api_key=_provider_api_key(provider, settings),
            model=provider.model,
            base_url=provider.base_url,
            provider=provider.name,
            timeout_seconds=provider.timeout_seconds,
            max_output_tokens=provider.max_tokens,
            supports_json=provider.supports_json,
        )
    if provider.kind == ProviderKind.HERMES_CLI:
        return HermesCliClient(
            provider.command_template.replace("{model}", provider.model),
            timeout_seconds=provider.timeout_seconds,
            provider=provider.name,
            model=provider.model,
        )
    if provider.kind == ProviderKind.HERMES_HTTP:
        return HermesHttpClient(base_url=provider.base_url, timeout_seconds=provider.timeout_seconds)
    raise ValueError(f"Unsupported provider kind: {provider.kind}")


def _provider_api_key(provider: ProviderSpec, settings) -> str:
    return {
        "OPENROUTER_API_KEY": settings.openrouter_api_key,
        "GROQ_API_KEY": settings.groq_api_key,
        "HF_API_KEY": settings.hf_api_key,
        "OPENAI_API_KEY": settings.openai_api_key,
    }.get(provider.credential_env, "")
