from __future__ import annotations

from types import SimpleNamespace

from assistant.provider_registry import ProviderCostMode, ProviderKind, build_provider_registry


def settings(**overrides):
    values = {
        "openrouter_api_key": "test-openrouter-key",
        "openrouter_model": "openrouter/free",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_timeout_seconds": 12.0,
        "openrouter_max_output_tokens": 500,
        "openai_api_key": "test-openai-key",
        "openai_model": "gpt-5-nano",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_max_output_tokens": 600,
        "hermes_cli_command_template": "hermes --provider openrouter --model {model} --oneshot {prompt}",
        "hermes_cli_models": ["openrouter/free"],
        "hermes_timeout_seconds": 25.0,
        "groq_api_key": "",
        "groq_model": "llama-3.1-8b-instant",
        "groq_base_url": "https://api.groq.com/openai/v1",
        "groq_timeout_seconds": 10.0,
        "groq_max_output_tokens": 500,
        "hf_api_key": "",
        "hf_model": "meta-llama/Llama-3.1-8B-Instruct",
        "hf_base_url": "https://router.huggingface.co/v1",
        "hf_timeout_seconds": 15.0,
        "hf_max_output_tokens": 500,
        "ai_allow_paid_fallback": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_provider_registry_describes_free_cheap_and_cli_providers() -> None:
    registry = build_provider_registry(settings())

    names = [provider.name for provider in registry.enabled()]
    openrouter = registry.get("openrouter_free")
    openai = registry.get("openai_cheap")
    cli = registry.get("hermes_cli_openrouter_free")

    assert names[:3] == ["openrouter_free", "openai_cheap", "hermes_cli_openrouter_free"]
    assert openrouter.model == "openrouter/free"
    assert openrouter.cost_mode == ProviderCostMode.FREE
    assert openrouter.kind == ProviderKind.OPENAI_CHAT
    assert openrouter.supports_json
    assert openai.cost_mode == ProviderCostMode.CHEAP
    assert cli.kind == ProviderKind.HERMES_CLI


def test_provider_registry_adds_optional_groq_and_hf_only_when_keys_exist() -> None:
    without_optional = build_provider_registry(settings(groq_api_key="", hf_api_key=""))
    with_optional = build_provider_registry(settings(groq_api_key="groq-key", hf_api_key="hf-key"))

    assert "groq" not in {provider.name for provider in without_optional.enabled()}
    assert "huggingface" not in {provider.name for provider in without_optional.enabled()}
    assert "groq" in {provider.name for provider in with_optional.enabled()}
    assert "huggingface" in {provider.name for provider in with_optional.enabled()}


def test_provider_registry_respects_free_only_cost_mode() -> None:
    registry = build_provider_registry(settings(openai_api_key="", ai_allow_paid_fallback=False))

    assert [provider.cost_mode for provider in registry.enabled()][0] == ProviderCostMode.FREE
    assert all(provider.cost_mode != ProviderCostMode.PAID for provider in registry.enabled())


def test_registry_does_not_label_non_free_openrouter_or_cli_models_as_free() -> None:
    registry = build_provider_registry(
        settings(
            openrouter_model="openai/gpt-5-nano",
            hermes_cli_models=["openrouter/free", "openai/gpt-5-nano"],
        )
    )

    assert registry.get("openrouter_free").cost_mode == ProviderCostMode.CHEAP
    assert registry.get("hermes_cli_openrouter_free").cost_mode == ProviderCostMode.FREE
    assert registry.get("hermes_cli_openai_gpt_5_nano").cost_mode == ProviderCostMode.CHEAP
