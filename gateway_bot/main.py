from __future__ import annotations

import logging
from pathlib import Path

from assistant.google_docs_sync import GoogleDocsWebhookSync, NullDocsSync
from assistant.google_sheets_sync import GoogleServiceAccountConfig, GoogleSheetsSync
from assistant.pipeline import AssistantPipeline
from assistant.provider_clients import (
    FakeHermesClient,
    HermesCliClient,
    HermesClient,
    HermesHttpClient,
    OpenAIChatCompletionsClient,
    OpenAIResponsesClient,
)
from assistant.provider_registry import ProviderKind, ProviderSpec, build_provider_registry
from assistant.provider_router import ProviderRouterClient
from assistant.task_command_center import TaskCommandCenter
from backend.config import Settings
from backend.db import init_db, make_session_factory
from backend.stores import (
    EventStore,
    SqlAgentJobStore,
    SqlActionQueueStore,
    SqlConversationStore,
    SqlDailyLimitStore,
    SqlDeliveryOutboxStore,
    SqlIdeaStore,
    SqlMemoryStore,
    SqlMonitorJobStore,
    SqlProviderHealthStore,
    SqlReminderStore,
    SqlTraceStore,
    SqlUserPreferenceStore,
    UserStore,
)
from gateway_bot.service import GatewayService


settings = Settings()
_gateway_service: GatewayService | None = None
_session_factory = None
logger = logging.getLogger(__name__)


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory(settings.database_url)
        init_db(_session_factory)
    return _session_factory


def build_hermes_client() -> HermesClient:
    session_factory = get_session_factory()
    provider_health = SqlProviderHealthStore(session_factory)
    if settings.hermes_mode == "fake":
        return FakeHermesClient()
    if settings.hermes_mode == "http":
        return HermesHttpClient(
            base_url=settings.hermes_api_url,
            path=settings.hermes_api_path,
            token=settings.hermes_api_token,
            timeout_seconds=settings.hermes_timeout_seconds,
        )
    if settings.hermes_mode == "cli":
        return HermesCliClient(
            settings.hermes_cli_command,
            timeout_seconds=settings.hermes_timeout_seconds,
        )
    if settings.hermes_mode in {"provider_router", "cli_router", "openai_router"}:
        registry = build_provider_registry(settings)
        return ProviderRouterClient(
            registry=registry,
            health_store=provider_health,
            client_factory=lambda provider: build_provider_client(provider, settings),
        )
    raise ValueError(f"Unsupported HERMES_MODE: {settings.hermes_mode}")


def build_provider_client(provider: ProviderSpec, settings: Settings) -> HermesClient:
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
        return HermesHttpClient(
            base_url=provider.base_url,
            timeout_seconds=provider.timeout_seconds,
        )
    raise ValueError(f"Unsupported provider kind: {provider.kind}")


def _provider_api_key(provider: ProviderSpec, settings: Settings) -> str:
    if provider.credential_env == "OPENROUTER_API_KEY":
        return settings.openrouter_api_key
    if provider.credential_env == "GROQ_API_KEY":
        return settings.groq_api_key
    if provider.credential_env == "HF_API_KEY":
        return settings.hf_api_key
    if provider.credential_env == "OPENAI_API_KEY":
        return settings.openai_api_key
    return ""


def build_pipeline() -> AssistantPipeline:
    session_factory = get_session_factory()
    docs_sync = build_docs_sync()
    event_store = EventStore(session_factory)
    return AssistantPipeline(
        hermes=build_hermes_client(),
        limits=SqlDailyLimitStore(
            session_factory,
            per_user_limit=settings.ai_daily_user_limit,
            global_limit=settings.ai_daily_global_limit,
        ),
        max_input_chars=settings.ai_max_input_chars,
        max_output_chars=settings.ai_max_output_chars,
        plain_text_ai_enabled=settings.ai_reply_to_plain_text,
        memories=SqlMemoryStore(session_factory),
        ideas=SqlIdeaStore(session_factory),
        reminders=SqlReminderStore(session_factory),
        docs_sync=docs_sync,
        task_center=build_task_center(),
        agent_jobs=SqlAgentJobStore(session_factory),
        conversation_turns=SqlConversationStore(session_factory),
        preferences=SqlUserPreferenceStore(session_factory),
        provider_health=SqlProviderHealthStore(session_factory),
        delivery_outbox=SqlDeliveryOutboxStore(session_factory),
        action_queue=SqlActionQueueStore(session_factory),
        events=event_store,
        monitor_jobs=SqlMonitorJobStore(session_factory),
    )


def build_docs_sync():
    if settings.enable_google_sheets_sync:
        try:
            return GoogleSheetsSync(
                GoogleServiceAccountConfig(
                    spreadsheet_id=settings.google_spreadsheet_id,
                    sheet_name=settings.google_assistant_sheet_name,
                    project_id=settings.google_project_id,
                    private_key_id=settings.google_private_key_id,
                    private_key=settings.google_private_key,
                    client_email=settings.google_client_email,
                    client_id=settings.google_client_id,
                    client_x509_cert_url=settings.google_client_x509_cert_url,
                )
            )
        except Exception:
            logger.exception("Google Sheets sync disabled")
            return NullDocsSync()
    if settings.google_docs_webhook_url:
        return GoogleDocsWebhookSync(
            settings.google_docs_webhook_url,
            token=settings.google_docs_webhook_token,
            timeout_seconds=settings.google_docs_webhook_timeout_seconds,
        )
    return NullDocsSync()


def build_task_center() -> TaskCommandCenter | None:
    if not settings.task_command_center_enabled:
        return None
    return TaskCommandCenter(
        root=Path(settings.task_command_center_dir),
        python_executable=settings.task_command_center_python,
        timeout_seconds=settings.task_command_center_timeout_seconds,
    )


def build_gateway_service() -> GatewayService:
    allowed = set(settings.allowed_tg_user_ids)
    allowed.update(settings.admin_tg_user_ids)
    event_store = EventStore(get_session_factory())
    return GatewayService(
        pipeline=build_pipeline(),
        allowed_tg_user_ids=allowed or None,
        admin_tg_user_ids=set(settings.admin_tg_user_ids) or None,
        users=UserStore(get_session_factory()),
        events=event_store,
        traces=SqlTraceStore(get_session_factory()),
    )


def get_gateway_service() -> GatewayService:
    global _gateway_service
    if _gateway_service is None:
        _gateway_service = build_gateway_service()
    return _gateway_service


def handle_local_text(tg_user_id: int, text: str) -> str:
    service = get_gateway_service()
    return service.handle_text(tg_user_id, text).text


if __name__ == "__main__":
    print("Run Telegram polling with: .venv/bin/python -m gateway_bot.telegram_app")
