from __future__ import annotations

import logging
from pathlib import Path

from assistant.google_docs_sync import GoogleDocsWebhookSync, NullDocsSync
from assistant.google_sheets_sync import GoogleServiceAccountConfig, GoogleSheetsSync
from assistant.pipeline import AssistantPipeline
from assistant.provider_clients import FakeHermesClient, HermesCliClient, HermesClient, HermesHttpClient
from assistant.provider_policy import ProviderSelectionPolicy, require_policy_controlled_transport
from assistant.provider_registry import build_provider_registry
from assistant.provider_router import ProviderRouterClient
from assistant.provider_transport import build_provider_client
from assistant.task_command_center import TaskCommandCenter
from backend.config import Settings
from backend.db import make_session_factory
from backend.migrations import require_current_schema
from backend.stores import (
    EventStore,
    SqlAgentJobStore,
    SqlActionQueueStore,
    SqlAutomationLeaseStore,
    SqlConversationStore,
    SqlDailyLimitStore,
    SqlDeliveryOutboxStore,
    SqlIdeaStore,
    SqlInboundUpdateStore,
    SqlMemoryStore,
    SqlMonitorJobStore,
    SqlProviderBudgetLedger,
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
        require_current_schema(settings.database_url)
    return _session_factory


def build_hermes_client() -> HermesClient:
    session_factory = get_session_factory()
    provider_health = SqlProviderHealthStore(session_factory)
    events = EventStore(session_factory)
    require_policy_controlled_transport(cost_mode=settings.ai_cost_mode, hermes_mode=settings.hermes_mode)
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
            policy=ProviderSelectionPolicy(
                cost_mode=settings.ai_cost_mode,
                deadline_seconds=settings.ai_provider_deadline_seconds,
                max_attempts=settings.ai_provider_max_attempts,
                cooldown_seconds=settings.ai_provider_cooldown_seconds,
                daily_budget_micro_usd=settings.ai_provider_daily_budget_micro_usd,
                minimum_quality_score=settings.ai_provider_min_quality_score,
                budget_ledger=SqlProviderBudgetLedger(session_factory),
            ),
            event_logger=lambda request, event_type, meta: events.log(
                request.user.user_id,
                event_type,
                meta,
                trace_id=request.trace_id,
            ),
        )
    raise ValueError(f"Unsupported HERMES_MODE: {settings.hermes_mode}")


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
        worker_leases=SqlAutomationLeaseStore(session_factory),
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
    if not settings.task_command_center_dir.strip():
        logger.warning("Task Command Center is enabled but TASK_COMMAND_CENTER_DIR is empty")
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
        inbound_updates=SqlInboundUpdateStore(get_session_factory()),
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
