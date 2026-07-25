from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from .action_plans import ActionPlanStore
from .capabilities import CapabilityPolicyStore
from .coding_jobs import NativeCodingJobStore
from .contacts import ContactStore
from .events import EventStore
from .github_public import FetchJson as GitHubPublicFetchJson
from .github_public import GitHubPublicReader
from .knowledge_archive import FetchBytes as KnowledgeFetchBytes
from .knowledge_archive import KnowledgeArchive
from .monitors import MonitorRegistry
from .memory_consolidation import MemoryConsolidator
from .personal_os import PersonalOSStore
from .personal_crm import PersonalCRMStore
from .personal_productivity import PersonalProductivityStore
from .personal_rhythms import PersonalRhythmStore
from .skill_distillation import SkillDistiller
from .shopping import ShoppingStore
from .subscriptions import SubscriptionStore, subscription_sync_from_env
from .system_status import collect_system_status
from .task_calendar import TaskCalendarAdapter
from .telegram_text_export import (
    ExportResult,
    FileDownloadResult,
    run_telegram_export,
    run_telegram_file_download,
)
from .tool_catalog import ToolBundle, discover_tool_specs, tool_catalog_entry
from .trips import TripStore
from .voice_inbox import VoiceVocabularyStore


AdapterFactory = Callable[[], Any]
Exporter = Callable[..., ExportResult]
FileDownloader = Callable[..., FileDownloadResult]
Confirmer = Callable[[str], Awaitable[bool]]
SubscriptionSync = Callable[[list[dict[str, Any]]], None]
PlanReceiptSender = Callable[[int, str], str | None]
logger = logging.getLogger(__name__)


def personal_os_database_path() -> Path:
    explicit = os.getenv("PERSONAL_OS_DB", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    return home / "data" / "personal-os.sqlite3"


def _value_payload(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_value_payload(item) for item in value]
    if isinstance(value, list):
        return [_value_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _value_payload(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {name: _value_payload(getattr(value, name)) for name in value.__dataclass_fields__}
    return value


from .mcp_api_plans import PlansMixin
from .mcp_api_personal import PersonalMixin
from .mcp_api_research import ResearchMixin
from .mcp_api_coding import CodingMixin


class NativeToolsAPI(PlansMixin, PersonalMixin, ResearchMixin, CodingMixin):
    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        adapter_factory: AdapterFactory = TaskCalendarAdapter.from_env,
        exporter: Exporter = run_telegram_export,
        file_downloader: FileDownloader = run_telegram_file_download,
        subscription_sync: SubscriptionSync | None = None,
        knowledge_fetcher: KnowledgeFetchBytes | None = None,
        github_public_fetcher: GitHubPublicFetchJson | None = None,
        plan_receipt_sender: PlanReceiptSender | None = None,
    ) -> None:
        self.database_path = Path(database_path or personal_os_database_path()).expanduser()
        self.adapter_factory = adapter_factory
        self._task_calendar_adapter: Any | None = None
        self._stores: dict[str, Any] = {}
        self.exporter = exporter
        self.file_downloader = file_downloader
        self.subscription_sync = subscription_sync if subscription_sync is not None else subscription_sync_from_env()
        self.knowledge_fetcher = knowledge_fetcher
        self.github_public_fetcher = github_public_fetcher
        self.plan_receipt_sender = plan_receipt_sender

    def integration_health(self) -> dict[str, bool]:
        self._capabilities().require("integration.health")
        health = self._task_calendar().health_check()
        return {
            "ok": bool(health.ok),
            "trello_ok": bool(health.trello_ok),
            "calendar_ok": bool(health.calendar_ok),
        }

    def system_status(self) -> dict[str, Any]:
        self._capabilities().require("system.status")
        result = collect_system_status(profile_home=os.getenv("HERMES_HOME", "~/.hermes"))
        try:
            integration = self.integration_health()
        except Exception:  # Status must stay available when an external integration is down.
            integration = {"ok": False, "trello_ok": False, "calendar_ok": False}
        result["integrations"] = integration
        return result

    def tool_catalog_discover(
        self,
        *,
        query: str = "",
        bundle: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Discover a focused subset; it never changes the active policy or bundles."""
        selected_bundle = ToolBundle(str(bundle)) if bundle else None
        mode = self._capabilities().get_mode().name
        items = []
        for spec in discover_tool_specs(query, bundle=selected_bundle, limit=limit):
            decisions = [self._capabilities().decide(capability) for capability in spec.capabilities]
            if any(decision.decision == "deny" for decision in decisions):
                continue
            items.append(tool_catalog_entry(spec))
        return {"mode": mode, "items": items}

    def task_list(self, *, list_name: str | None = None) -> dict[str, str]:
        self._capabilities().require("task.list")
        return {"items": self._task_calendar().list_tasks(list_name=list_name)}

    def calendar_list(self, *, when: str = "today") -> dict[str, str]:
        self._capabilities().require("calendar.list")
        return {"items": self._task_calendar().list_calendar_events(when=when)}

    def task_dashboard(self) -> dict[str, Any]:
        self._capabilities().require("task.list")
        return self._task_calendar().dashboard_tasks()

    def calendar_dashboard(self, *, days: int = 7) -> dict[str, Any]:
        self._capabilities().require("calendar.list")
        return self._task_calendar().dashboard_calendar(days=days)

    def capability_decision(self, *, capability: str) -> dict[str, Any]:
        return _value_payload(self._capabilities().decide(capability))

    def _plans(self) -> ActionPlanStore:
        return self._store("plans", lambda: ActionPlanStore(self.database_path, event_store=self._events()))

    def _contacts(self) -> ContactStore:
        return self._store("contacts", lambda: ContactStore(self.database_path))

    def _monitors(self) -> MonitorRegistry:
        return self._store("monitors", lambda: MonitorRegistry(self.database_path))

    def _knowledge(self) -> KnowledgeArchive:
        return self._store("knowledge", lambda: KnowledgeArchive(self.database_path, fetcher=self.knowledge_fetcher))

    def _github_public(self) -> GitHubPublicReader:
        return self._store("github_public", lambda: GitHubPublicReader(fetcher=self.github_public_fetcher))

    def _skills(self) -> SkillDistiller:
        return self._store("skills", lambda: SkillDistiller(self.database_path))

    def _memory_consolidator(self) -> MemoryConsolidator:
        return self._store("memory_consolidator", lambda: MemoryConsolidator(self.database_path))

    def _personal_os(self) -> PersonalOSStore:
        return self._store("personal_os", lambda: PersonalOSStore(self.database_path))

    def _productivity(self) -> PersonalProductivityStore:
        return self._store("productivity", lambda: PersonalProductivityStore(self.database_path))

    def _crm(self) -> PersonalCRMStore:
        return self._store("crm", lambda: PersonalCRMStore(self.database_path))

    def _rhythms(self) -> PersonalRhythmStore:
        return self._store("rhythms", lambda: PersonalRhythmStore(self.database_path))

    def _subscriptions(self) -> SubscriptionStore:
        return self._store("subscriptions", lambda: SubscriptionStore(self.database_path))

    def _shopping(self) -> ShoppingStore:
        return self._store("shopping", lambda: ShoppingStore(self.database_path))

    def _trips(self) -> TripStore:
        return self._store("trips", lambda: TripStore(self.database_path))

    def _coding_jobs(self) -> NativeCodingJobStore:
        return self._store("coding_jobs", lambda: NativeCodingJobStore(self.database_path, event_store=self._events()))

    def _voice_vocabulary(self) -> VoiceVocabularyStore:
        return self._store("voice_vocabulary", lambda: VoiceVocabularyStore(self.database_path))

    def _capabilities(self) -> CapabilityPolicyStore:
        return self._store("capabilities", lambda: CapabilityPolicyStore(self.database_path))

    def _events(self) -> EventStore:
        return self._store("events", lambda: EventStore(self.database_path))

    def _action_adapter(self) -> "_NativeActionAdapter":
        return self._store(
            "action_adapter",
            lambda: _NativeActionAdapter(
                self._task_calendar,
                self._personal_os(),
                self._productivity(),
            ),
        )

    def _store(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self._stores:
            self._stores[name] = factory()
        return self._stores[name]

    def _task_calendar(self) -> Any:
        if self._task_calendar_adapter is None:
            self._task_calendar_adapter = self.adapter_factory()
        return self._task_calendar_adapter


class _NativeActionAdapter:
    def __init__(
        self,
        task_calendar_factory: AdapterFactory,
        personal_os: PersonalOSStore,
        productivity: PersonalProductivityStore,
    ) -> None:
        self.task_calendar_factory = task_calendar_factory
        self._task_calendar: Any | None = None
        self.personal_os = personal_os
        self.productivity = productivity

    def __getattr__(self, name: str) -> Any:
        if self._task_calendar is None:
            self._task_calendar = self.task_calendar_factory()
        return getattr(self._task_calendar, name)

    def execute_batch(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._task_calendar is None:
            self._task_calendar = self.task_calendar_factory()
        handler = getattr(self._task_calendar, "execute_batch", None)
        if callable(handler):
            return handler(actions)
        handlers = {
            "task.create": "create_task",
            "task.move": "move_task",
            "task.priority": "set_task_priority",
            "task.done": "complete_task",
            "task.delete": "delete_task",
            "calendar.create": "create_calendar_event",
            "calendar.move": "move_calendar_event",
            "calendar.delete": "delete_calendar_event",
        }
        results: list[dict[str, Any]] = []
        for action in actions:
            try:
                result = getattr(self._task_calendar, handlers[str(action.get("type") or "")])(
                    **action.get("payload", {})
                )
            except Exception as error:
                results.append({"ok": False, "error": str(error) or type(error).__name__})
            else:
                results.append({"ok": True, "result": str(result)})
        return results

    def save_note(self, **payload: Any) -> str:
        note = self.personal_os.upsert_memory_block(block_type="note", **payload)
        return f"saved note\nnote_id={note.id}"

    def create_commitment(self, **payload: Any) -> str:
        commitment = self.personal_os.create_commitment(**payload)
        if commitment.due_at:
            self.productivity.create_reminder(
                text=f"Срок обещания: {commitment.subject} — {commitment.content}",
                remind_at=commitment.due_at,
                idempotency_key=f"commitment:{commitment.id}:due",
                source_type="commitment",
                source_id=commitment.id,
            )
        return f"created commitment\ncommitment_id={commitment.id}"

    def create_reminder(self, **payload: Any) -> str:
        reminder = self.productivity.create_reminder(**payload)
        return f"created reminder\nreminder_id={reminder.id}"
