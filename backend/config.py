from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency is installed for runtime.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_set_env(name: str) -> set[int]:
    value = os.getenv(name, "")
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result


def _csv_env(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "telegram-ai-brooch")
    app_env: str = os.getenv("APP_ENV", "development")
    git_commit: str = os.getenv("GIT_COMMIT", "unknown")
    build_time: str = os.getenv("BUILD_TIME", "unknown")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/ai_brooch.sqlite3")
    bot_token: str = os.getenv("BOT_TOKEN", "")
    assistant_service_token: str = os.getenv("ASSISTANT_SERVICE_TOKEN", "")
    admin_tg_user_ids: set[int] = None  # type: ignore[assignment]
    allowed_tg_user_ids: set[int] = None  # type: ignore[assignment]
    ai_enabled: bool = _bool_env("AI_ENABLED", True)
    ai_cost_mode: str = os.getenv("AI_COST_MODE", "free_only")
    ai_allow_paid_fallback: bool = _bool_env("AI_ALLOW_PAID_FALLBACK", False)
    ai_reply_to_plain_text: bool = _bool_env("AI_REPLY_TO_PLAIN_TEXT", True)
    ai_daily_user_limit: int = int(os.getenv("AI_DAILY_USER_LIMIT", "0"))
    ai_daily_global_limit: int = int(os.getenv("AI_DAILY_GLOBAL_LIMIT", "0"))
    ai_max_input_chars: int = int(os.getenv("AI_MAX_INPUT_CHARS", "4000"))
    ai_max_output_chars: int = int(os.getenv("AI_MAX_OUTPUT_CHARS", "2500"))
    ai_style_enabled: bool = _bool_env("AI_STYLE_ENABLED", True)
    ai_style_prompt_path: str = os.getenv("AI_STYLE_PROMPT_PATH", "")
    ai_provider_deadline_seconds: float = float(
        os.getenv("AI_PROVIDER_DEADLINE_SECONDS", os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "15"))
    )
    ai_provider_max_attempts: int = int(os.getenv("AI_PROVIDER_MAX_ATTEMPTS", "2"))
    ai_provider_cooldown_seconds: int = int(os.getenv("AI_PROVIDER_COOLDOWN_SECONDS", "120"))
    ai_provider_daily_budget_micro_usd: int = int(os.getenv("AI_PROVIDER_DAILY_BUDGET_MICRO_USD", "0"))
    ai_provider_min_quality_score: int = int(os.getenv("AI_PROVIDER_MIN_QUALITY_SCORE", "60"))
    openai_estimated_cost_micro_usd: int = int(os.getenv("OPENAI_ESTIMATED_COST_MICRO_USD", "1000"))
    paid_estimated_cost_micro_usd: int = int(os.getenv("PAID_ESTIMATED_COST_MICRO_USD", "10000"))
    hermes_mode: str = os.getenv("HERMES_MODE", "fake")
    hermes_api_url: str = os.getenv("HERMES_API_URL", "http://127.0.0.1:8765")
    hermes_api_path: str = os.getenv("HERMES_API_PATH", "/api/chat")
    hermes_api_token: str = os.getenv("HERMES_API_TOKEN", "")
    hermes_cli_command: str = os.getenv("HERMES_CLI_COMMAND", "hermes --oneshot {prompt}")
    hermes_cli_command_template: str = os.getenv(
        "HERMES_CLI_COMMAND_TEMPLATE",
        "hermes --provider openrouter --model {model} --oneshot {prompt}",
    )
    hermes_cli_models: list[str] = None  # type: ignore[assignment]
    hermes_cli_enabled: bool = _bool_env("HERMES_CLI_ENABLED", True)
    hermes_paid_fallback_models: list[str] = None  # type: ignore[assignment]
    hermes_timeout_seconds: float = float(os.getenv("HERMES_TIMEOUT_SECONDS", "25"))
    hermes_tools_enabled: bool = _bool_env("HERMES_TOOLS_ENABLED", False)
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_enabled: bool = _bool_env("OPENROUTER_ENABLED", True)
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openrouter/free")
    openrouter_timeout_seconds: float = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "12"))
    openrouter_max_output_tokens: int = int(os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS", "500"))
    openrouter_estimated_cost_micro_usd: int = int(os.getenv("OPENROUTER_ESTIMATED_COST_MICRO_USD", "1000"))
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5-nano")
    openai_max_output_tokens: int = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "600"))
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    groq_timeout_seconds: float = float(os.getenv("GROQ_TIMEOUT_SECONDS", "10"))
    groq_max_output_tokens: int = int(os.getenv("GROQ_MAX_OUTPUT_TOKENS", "500"))
    hf_api_key: str = os.getenv("HF_API_KEY") or os.getenv("HF_TOKEN", "")
    hf_base_url: str = os.getenv("HF_BASE_URL", "https://router.huggingface.co/v1")
    hf_model: str = os.getenv("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    hf_timeout_seconds: float = float(os.getenv("HF_TIMEOUT_SECONDS", "15"))
    hf_max_output_tokens: int = int(os.getenv("HF_MAX_OUTPUT_TOKENS", "500"))
    hermes_cli_estimated_cost_micro_usd: int = int(os.getenv("HERMES_CLI_ESTIMATED_COST_MICRO_USD", "1000"))
    openai_transcribe_model: str = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    voice_max_bytes: int = int(os.getenv("VOICE_MAX_BYTES", "10485760"))
    telegram_blocking_max_concurrency: int = int(os.getenv("TELEGRAM_BLOCKING_MAX_CONCURRENCY", "4"))
    telegram_blocking_timeout_seconds: float = float(os.getenv("TELEGRAM_BLOCKING_TIMEOUT_SECONDS", "60"))
    telegram_fast_ack_seconds: float = float(os.getenv("TELEGRAM_FAST_ACK_SECONDS", "0.6"))
    training_feedback_buttons_enabled: bool = _bool_env("TRAINING_FEEDBACK_BUTTONS_ENABLED", False)
    google_docs_webhook_url: str = os.getenv("GOOGLE_DOCS_WEBHOOK_URL", "")
    google_docs_webhook_token: str = os.getenv("GOOGLE_DOCS_WEBHOOK_TOKEN", "")
    google_docs_webhook_timeout_seconds: float = float(os.getenv("GOOGLE_DOCS_WEBHOOK_TIMEOUT_SECONDS", "5"))
    enable_google_sheets_sync: bool = _bool_env("ENABLE_GOOGLE_SHEETS_SYNC", False)
    google_spreadsheet_id: str = os.getenv("GOOGLE_SPREADSHEET_ID", "")
    google_assistant_sheet_name: str = os.getenv("GOOGLE_ASSISTANT_SHEET_NAME", "AI Brooch")
    google_project_id: str = os.getenv("GOOGLE_PROJECT_ID", "")
    google_private_key_id: str = os.getenv("GOOGLE_PRIVATE_KEY_ID", "")
    google_private_key: str = os.getenv("GOOGLE_PRIVATE_KEY", "")
    google_client_email: str = os.getenv("GOOGLE_CLIENT_EMAIL", "")
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_x509_cert_url: str = os.getenv("GOOGLE_CLIENT_X509_CERT_URL", "")
    task_command_center_enabled: bool = _bool_env("TASK_COMMAND_CENTER_ENABLED", True)
    task_command_center_dir: str = os.getenv("TASK_COMMAND_CENTER_DIR", "")
    task_command_center_python: str = os.getenv("TASK_COMMAND_CENTER_PYTHON", ".venv/bin/python")
    task_command_center_timeout_seconds: float = float(os.getenv("TASK_COMMAND_CENTER_TIMEOUT_SECONDS", "45"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "admin_tg_user_ids", _int_set_env("ADMIN_TG_USER_IDS"))
        object.__setattr__(self, "allowed_tg_user_ids", _int_set_env("ALLOWED_TG_USER_IDS"))
        object.__setattr__(self, "hermes_cli_models", _csv_env("HERMES_CLI_MODELS", "openrouter/free"))
        object.__setattr__(
            self,
            "hermes_paid_fallback_models",
            _csv_env("HERMES_PAID_FALLBACK_MODELS", "google/gemini-2.5-flash-lite"),
        )
