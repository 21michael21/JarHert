from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .action_plans import ActionPlan, ActionPlanStore, execute_plan
from .capabilities import CapabilityPolicyStore
from .coding_jobs import NativeCodingJobStore
from .contacts import ContactStore, MessagePlan
from .delivery import HermesTelegramSender
from .github_public import FetchJson as GitHubPublicFetchJson
from .github_public import GitHubPublicReader
from .knowledge_archive import FetchBytes as KnowledgeFetchBytes
from .knowledge_archive import KnowledgeArchive
from .monitors import Monitor, MonitorRegistry
from .memory_consolidation import MemoryConsolidator
from .personal_os import PersonalOSStore
from .personal_crm import PersonalCRMStore
from .personal_productivity import PersonalProductivityStore, local_day_bounds
from .personal_rhythms import PersonalRhythmStore, format_daily_brief
from .skill_distillation import SkillDistiller
from .shopping import ShoppingStore
from .subscriptions import SubscriptionStore, subscription_sync_from_env
from .system_status import collect_system_status
from .task_calendar import TaskCalendarAdapter
from .telegram_text_export import (
    ExportResult,
    FileDownloadResult,
    read_export_for_analysis,
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


class NativeToolsAPI:
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

    def contact_add(self, *, name: str, telegram_chat_id: int, aliases: list[str]) -> dict[str, Any]:
        self._capabilities().require("contact.write")
        return _value_payload(
            self._contacts().add_contact(
                name=name,
                telegram_chat_id=telegram_chat_id,
                aliases=aliases,
            )
        )

    def contact_list(self) -> dict[str, Any]:
        self._capabilities().require("contact.list")
        return {"items": [_value_payload(item) for item in self._contacts().list_contacts()]}

    async def message_plan_confirm_schedule(
        self,
        *,
        items: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        self._capabilities().require("message.schedule")
        store = self._contacts()
        plan = store.create_message_plan(items, idempotency_key=idempotency_key)
        if plan.status != "draft":
            return _message_plan_payload(plan)
        if not await confirmer(_message_plan_preview(plan)):
            return _message_plan_payload(store.cancel_message_plan(plan.id))
        return _message_plan_payload(store.approve_message_plan(plan.id))

    def message_plan_cancel(self, *, plan_id: int) -> dict[str, Any]:
        self._capabilities().require("message.cancel")
        return _message_plan_payload(self._contacts().cancel_message_plan(plan_id))

    def monitor_add_github_releases(
        self,
        *,
        name: str,
        owner: str,
        repo: str,
        condition: str,
    ) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        return _monitor_payload(
            self._monitors().add(
                name=name,
                source_type="github_releases",
                source_config={"owner": owner, "repo": repo},
                condition=condition,
            )
        )

    def monitor_add_source(
        self,
        *,
        name: str,
        source_type: str,
        url: str,
        allowed_hosts: list[str],
        condition: str,
        quiet_hours: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        source_config: dict[str, Any] = {
            "url": url,
            "allowed_hosts": allowed_hosts,
            "timezone": timezone_name,
        }
        if quiet_hours:
            source_config["quiet_hours"] = quiet_hours
        return _monitor_payload(
            self._monitors().add(
                name=name,
                source_type=source_type,
                source_config=source_config,
                condition=condition,
            )
        )

    def monitor_list(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return {"items": [_monitor_payload(item) for item in self._monitors().list()]}

    def monitor_disable(self, *, monitor_id: int) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        return _monitor_payload(self._monitors().disable(monitor_id))

    def monitor_schedule_update(
        self,
        *,
        monitor_id: int,
        quiet_hours: str | None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        """Keep changed monitor events quiet and collect them for the existing digest."""
        self._capabilities().require("monitor.write")
        return _monitor_payload(
            self._monitors().update_schedule(
                monitor_id,
                quiet_hours=quiet_hours,
                timezone_name=timezone_name,
            )
        )

    def monitor_digest(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return self._monitors().build_digest()

    def monitor_digest_mark_delivered(self, *, item_ids: list[int]) -> dict[str, int]:
        self._capabilities().require("monitor.write")
        self._monitors().mark_digest_delivered(item_ids)
        return {"delivered": len(set(int(item_id) for item_id in item_ids))}

    def knowledge_archive_url(self, *, url: str, project: str | None = None) -> dict[str, Any]:
        self._capabilities().require("knowledge.write")
        return self._knowledge().archive_url(url, project=project)

    def knowledge_archive_urls(self, *, urls: list[str], project: str | None = None) -> dict[str, Any]:
        self._capabilities().require("knowledge.write")
        if not urls or len(urls) > 20:
            raise ValueError("Для архива укажи от 1 до 20 явных URL.")
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        archive = self._knowledge()
        for url in urls:
            try:
                items.append(archive.archive_url(str(url), project=project))
            except (OSError, ValueError) as error:
                errors.append({"url": str(url), "error": type(error).__name__})
        return {"items": items, "errors": errors}

    def knowledge_search(
        self,
        *,
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return {"items": self._knowledge().search(query, project=project, limit=limit)}

    def knowledge_source_excerpt(
        self,
        *,
        source_id: int,
        query: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return self._knowledge().source_excerpt(source_id, query=query)

    def knowledge_list_sources(
        self,
        *,
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return {"items": [_value_payload(item) for item in self._knowledge().list_sources(project=project, limit=limit)]}

    def github_public_repository(self, *, url: str) -> dict[str, Any]:
        self._capabilities().require("github.read")
        return self._github_public().inspect_repository(url)

    def shopping_add(
        self,
        *,
        text: str,
        idempotency_key: str,
        category: str | None = None,
        quantity: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("shopping.write")
        return _value_payload(
            self._shopping().add(
                text=text,
                category=category,
                quantity=quantity,
                project=project,
                idempotency_key=idempotency_key,
            )
        )

    def shopping_list(
        self,
        *,
        status: str = "needed",
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("shopping.read")
        return {"items": [_value_payload(item) for item in self._shopping().list(status=status, project=project, limit=limit)]}

    def shopping_mark_bought(self, *, item_id: int) -> dict[str, Any]:
        self._capabilities().require("shopping.write")
        return _value_payload(self._shopping().mark_bought(item_id))

    def shopping_remove(self, *, item_id: int) -> dict[str, Any]:
        self._capabilities().require("shopping.write")
        return _value_payload(self._shopping().remove(item_id))

    def trip_create(
        self,
        *,
        name: str,
        destination: str,
        idempotency_key: str,
        starts_at: str | None = None,
        ends_at: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("trip.write")
        return _value_payload(
            self._trips().create(
                name=name,
                destination=destination,
                starts_at=starts_at,
                ends_at=ends_at,
                idempotency_key=idempotency_key,
            )
        )

    def trip_list(self, *, status: str = "active", limit: int = 100) -> dict[str, Any]:
        self._capabilities().require("trip.read")
        return {"items": [_value_payload(item) for item in self._trips().list(status=status, limit=limit)]}

    def trip_details(self, *, trip_id: int) -> dict[str, Any]:
        self._capabilities().require("trip.read")
        return {
            "trip": _value_payload(self._trips().get(trip_id)),
            "items": [_value_payload(item) for item in self._trips().list_items(trip_id)],
        }

    def trip_add_item(
        self,
        *,
        trip_id: int,
        kind: str,
        title: str,
        idempotency_key: str,
        details: str | None = None,
        due_at: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("trip.write")
        item = self._trips().add_item(
            trip_id=trip_id,
            kind=kind,
            title=title,
            details=details,
            due_at=due_at,
            idempotency_key=idempotency_key,
        )
        if item.due_at:
            self._productivity().sync_source_reminder(
                source_type="trip_item",
                source_id=item.id,
                text=f"Поездка: {item.title}",
                remind_at=item.due_at,
                idempotency_key=f"trip-item:{item.id}:due",
            )
        return _value_payload(item)

    def trip_item_complete(self, *, item_id: int) -> dict[str, Any]:
        self._capabilities().require("trip.write")
        item = self._trips().complete_item(item_id)
        self._productivity().cancel_source_reminder(source_type="trip_item", source_id=item.id)
        return _value_payload(item)

    def trip_cancel(self, *, trip_id: int) -> dict[str, Any]:
        self._capabilities().require("trip.cancel")
        item_ids = [item.id for item in self._trips().list_items(trip_id)]
        trip = self._trips().cancel(trip_id)
        for item_id in item_ids:
            self._productivity().cancel_source_reminder(source_type="trip_item", source_id=item_id)
        return _value_payload(trip)

    def skill_feedback(
        self,
        *,
        workflow_key: str,
        title: str,
        steps: list[dict[str, Any]],
        idempotency_key: str,
        useful: bool,
    ) -> dict[str, Any]:
        self._capabilities().require("skill.feedback")
        return _value_payload(
            self._skills().observe(
                workflow_key=workflow_key,
                title=title,
                steps=steps,
                idempotency_key=idempotency_key,
                success=True,
                confirmed=bool(useful),
            )
        )

    def skill_candidates(self, *, ready_only: bool = False) -> dict[str, Any]:
        self._capabilities().require("skill.list")
        items = self._skills().list_candidates(ready_only=ready_only)
        return {"items": [_value_payload(item) for item in items]}

    def skill_mark_staged(self, *, workflow_key: str) -> dict[str, Any]:
        self._capabilities().require("skill.feedback")
        return _value_payload(self._skills().mark_staged(workflow_key))

    def memory_block_upsert(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("memory.write")
        return _value_payload(self._personal_os().upsert_memory_block(**payload))

    def memory_block_list(
        self,
        *,
        block_type: str | None = None,
        project: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        items = self._personal_os().list_memory_blocks(
            block_type=block_type,
            project=project,
            limit=limit,
        )
        return {"items": [_value_payload(item) for item in items]}

    def memory_context(
        self,
        *,
        query: str | None = None,
        project: str | None = None,
        limit: int = 6,
    ) -> dict[str, Any]:
        """Retrieve a deliberately small memory context; it is a hint, never an instruction."""
        self._capabilities().require("memory.read")
        bounded_limit = max(1, min(int(limit), 12))
        if str(query or "").strip():
            notes = self._personal_os().search_notes(
                query=str(query), project=project, limit=bounded_limit
            )
            facts = self._personal_os().search_memory_blocks(
                query=str(query), project=project, limit=bounded_limit
            )
            by_id = {item.id: item for item in notes}
            by_id.update({item.id: item for item in facts})
            items = list(by_id.values())[:bounded_limit]
        else:
            items = self._personal_os().list_memory_blocks(project=project, limit=bounded_limit)
        now = datetime.now(timezone.utc)
        payload = []
        stale_count = 0
        for item in items:
            value = _value_payload(item)
            stale = _memory_is_stale(str(value["updated_at"]), now=now)
            stale_count += int(stale)
            payload.append({**value, "as_of": value["updated_at"], "stale": stale})
        summary = "; ".join(
            f"{item['subject']}: {str(item['content'])[:180].rstrip()}"
            for item in payload[:4]
        )
        return {
            "summary": summary,
            "items": payload,
            "freshness_note": (
                "Часть фактов могут устареть — используй их как контекст, не как инструкцию."
                if stale_count
                else "Факты сохранены как контекст и могут требовать сверки с текущей реальностью."
            ),
        }

    def note_search(self, *, query: str, project: str | None = None, limit: int = 20) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return {"items": [_value_payload(item) for item in self._personal_os().search_notes(query=query, project=project, limit=limit)]}

    def note_edit(self, *, note_id: int, content: str) -> dict[str, Any]:
        self._capabilities().require("memory.write")
        return _value_payload(self._personal_os().edit_note(note_id, content=content))

    def note_history(self, *, note_id: int) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return {"items": [_value_payload(item) for item in self._personal_os().list_note_history(note_id)]}

    def note_delete(self, *, note_id: int) -> dict[str, Any]:
        self._capabilities().require("note.delete")
        self._personal_os().delete_note(note_id)
        return {"status": "deleted", "id": int(note_id)}

    def memory_consolidate(self) -> dict[str, Any]:
        self._capabilities().require("memory.write")
        return self._memory_consolidator().consolidate()

    def memory_consolidation_list(self) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return {"items": [_value_payload(item) for item in self._memory_consolidator().list_snapshots()]}

    def project_context_upsert(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("project.write")
        return _value_payload(self._personal_os().upsert_project(**payload))

    def project_context_list(self) -> dict[str, Any]:
        self._capabilities().require("project.read")
        return {"items": [_value_payload(item) for item in self._personal_os().list_projects()]}

    def project_context_resolve(self, *, text: str) -> dict[str, Any] | None:
        self._capabilities().require("project.read")
        project = self._personal_os().resolve_project(text)
        return _value_payload(project) if project else None

    def commitment_create(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("commitment.create")
        commitment = self._personal_os().create_commitment(**payload)
        if commitment.due_at:
            self._productivity().create_reminder(
                text=f"Срок обещания: {commitment.subject} — {commitment.content}",
                remind_at=commitment.due_at,
                idempotency_key=f"commitment:{commitment.id}:due",
                source_type="commitment",
                source_id=commitment.id,
            )
        return _value_payload(commitment)

    def commitment_list(
        self,
        *,
        contact: str | None = None,
        project: str | None = None,
        status: str = "open",
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("commitment.list")
        items = self._personal_os().list_commitments(
            contact=contact,
            project=project,
            status=status,
            limit=limit,
        )
        return {"items": [_value_payload(item) for item in items]}

    def commitment_complete(self, *, commitment_id: int) -> dict[str, Any]:
        self._capabilities().require("commitment.complete")
        commitment = self._personal_os().complete_commitment(commitment_id)
        self._productivity().cancel_source_reminder(
            source_type="commitment",
            source_id=commitment.id,
        )
        return _value_payload(commitment)

    def reminder_create(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("reminder.create")
        return _value_payload(self._productivity().create_reminder(**payload))

    def reminder_list(self, *, status: str = "active", limit: int = 100) -> dict[str, Any]:
        self._capabilities().require("reminder.list")
        items = self._productivity().list_reminders(status=status, limit=limit)
        return {"items": [_value_payload(item) for item in items]}

    def reminder_reschedule(
        self,
        *,
        reminder_id: int,
        remind_at: str,
        recurrence: str | None = "keep",
    ) -> dict[str, Any]:
        self._capabilities().require("reminder.write")
        return _value_payload(
            self._productivity().reschedule_reminder(
                reminder_id,
                remind_at=remind_at,
                recurrence=recurrence,
            )
        )

    def reminder_cancel(self, *, reminder_id: int) -> dict[str, Any]:
        self._capabilities().require("reminder.write")
        return _value_payload(self._productivity().cancel_reminder(reminder_id))

    def crm_interaction_log(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("crm.write")
        interaction = self._crm().log_interaction(**payload)
        if interaction.next_contact_at:
            self._productivity().create_reminder(
                text=f"Написать {interaction.contact}: {interaction.summary}",
                remind_at=interaction.next_contact_at,
                idempotency_key=f"crm-interaction:{interaction.id}:followup",
                source_type="crm_interaction",
                source_id=interaction.id,
            )
        return _value_payload(interaction)

    def crm_timeline(
        self,
        *,
        contact: str | None = None,
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("crm.read")
        items = self._crm().list_interactions(
            contact=contact,
            project=project,
            limit=limit,
        )
        return {"items": [_value_payload(item) for item in items]}

    def personal_today(
        self,
        *,
        now: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        self._capabilities().require("personal.read")
        start, end = local_day_bounds(now, timezone_name)
        reminders = self._productivity().reminders_between(start=start, end=end)
        followups = self._crm().followups_between(start=start, end=end)
        commitments = [
            item
            for item in self._personal_os().list_commitments(status="open")
            if item.due_at and start <= item.due_at < end
        ]
        adapter = self._task_calendar()
        errors: dict[str, str] = {}
        try:
            tasks = adapter.list_tasks(list_name="Today")
        except Exception as error:
            tasks = ""
            errors["tasks"] = str(error)[:200]
        try:
            calendar = adapter.list_calendar_events(when="today")
        except Exception as error:
            calendar = ""
            errors["calendar"] = str(error)[:200]
        priorities = [
            *(
                {"type": "reminder", "id": item.id, "title": item.text, "due_at": item.remind_at}
                for item in reminders
            ),
            *(
                {"type": "commitment", "id": item.id, "title": item.subject, "due_at": item.due_at}
                for item in commitments
            ),
            *(
                {
                    "type": "followup",
                    "id": item.id,
                    "title": f"Написать {item.contact}",
                    "due_at": item.next_contact_at,
                }
                for item in followups
            ),
        ]
        priorities.sort(key=lambda item: (str(item["due_at"]), str(item["type"]), int(item["id"])))
        return {
            "date_start": start,
            "timezone": timezone_name,
            "tasks": tasks,
            "calendar": calendar,
            "reminders": [_value_payload(item) for item in reminders],
            "commitments": [_value_payload(item) for item in commitments],
            "followups": [_value_payload(item) for item in followups],
            "top_three": priorities[:3],
            "integration_errors": errors,
        }

    def personal_daily_brief(
        self,
        *,
        now: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        data = self.personal_today(now=now, timezone_name=timezone_name)
        return {"text": format_daily_brief(data), "data": data}

    def personal_weekly_review(
        self,
        *,
        now: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        self._capabilities().require("personal.read")
        return self._rhythms().weekly_review(now=now, timezone_name=timezone_name)

    def subscription_create(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("subscription.write")
        item, created = self._subscriptions().create(**payload)
        self._productivity().sync_source_reminder(
            source_type="subscription",
            source_id=item.id,
            text=f"Списание {item.name}: {item.amount} {item.currency}",
            remind_at=item.next_charge_at,
            idempotency_key=f"subscription:{item.id}:charge",
        )
        if created:
            self._sync_subscriptions()
        return _value_payload(item)

    def subscription_list(self, *, status: str = "active") -> dict[str, Any]:
        self._capabilities().require("subscription.read")
        return {
            "items": [_value_payload(item) for item in self._subscriptions().list(status=status)],
            "monthly_totals": self._subscriptions().monthly_totals() if status == "active" else {},
        }

    def subscription_update(self, *, subscription_id: int, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("subscription.write")
        item = self._subscriptions().update(subscription_id, **payload)
        self._productivity().sync_source_reminder(
            source_type="subscription",
            source_id=item.id,
            text=f"Списание {item.name}: {item.amount} {item.currency}",
            remind_at=item.next_charge_at,
            idempotency_key=f"subscription:{item.id}:charge",
        )
        self._sync_subscriptions()
        return _value_payload(item)

    def subscription_cancel(self, *, subscription_id: int) -> dict[str, Any]:
        self._capabilities().require("subscription.write")
        item = self._subscriptions().cancel(subscription_id)
        self._productivity().cancel_source_reminder(source_type="subscription", source_id=item.id)
        self._sync_subscriptions()
        return _value_payload(item)

    def work_mode_get(self) -> dict[str, Any]:
        return _value_payload(self._capabilities().get_mode())

    def work_mode_set(self, *, mode: str) -> dict[str, Any]:
        return _value_payload(self._capabilities().set_mode(mode))

    def voice_inbox_prepare(self, *, transcript: str) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return _value_payload(self._voice_vocabulary().prepare(transcript))

    def voice_vocabulary_add(self, *, spoken: str, canonical: str) -> dict[str, Any]:
        self._capabilities().require("memory.write")
        return _value_payload(self._voice_vocabulary().add(spoken=spoken, canonical=canonical))

    def voice_vocabulary_list(self) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return {"items": _value_payload(self._voice_vocabulary().list())}

    def coding_job_enqueue(
        self,
        *,
        mode: str,
        prompt: str,
        idempotency_key: str,
        repository_url: str | None = None,
        source_urls: list[str] | None = None,
        source_text: str | None = None,
        source_label: str | None = None,
        followups: list[str] | None = None,
    ) -> dict[str, Any]:
        capability = "coding.queue" if mode == "coding" else "research.run"
        self._capabilities().require(capability)
        tg_user_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
        if tg_user_id <= 0:
            raise RuntimeError("HERMES_OWNER_TELEGRAM_CHAT_ID is required")
        if followups:
            if source_text is not None or source_label is not None:
                raise ValueError("Follow-up coding jobs do not accept an attached text export.")
            jobs = self._coding_jobs().enqueue_chain(
                tg_user_id=tg_user_id,
                mode=mode,
                prompt=prompt,
                repository_url=repository_url,
                source_urls=list(source_urls or []),
                followups=followups,
                idempotency_key=idempotency_key,
            )
            payload = _value_payload(jobs[0])
            payload["followup_job_ids"] = [job.id for job in jobs[1:]]
            return payload
        return _value_payload(self._coding_jobs().enqueue(
            tg_user_id=tg_user_id,
            mode=mode,
            prompt=prompt,
            repository_url=repository_url,
            source_urls=list(source_urls or []),
            source_text=source_text,
            source_label=source_label,
            idempotency_key=idempotency_key,
        ))

    def telegram_text_export_excerpt(self, *, path: str, max_chars: int = 120_000) -> dict[str, Any]:
        self._capabilities().require("telegram.export.read")
        result = read_export_for_analysis(path, max_chars=max_chars)
        return {
            "path": str(result.path),
            "text": result.text,
            "source_chars": result.source_chars,
            "truncated": result.truncated,
        }

    def telegram_text_export_queue_analysis(
        self,
        *,
        path: str,
        question: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._capabilities().require("telegram.export.read")
        result = read_export_for_analysis(path)
        return self.coding_job_enqueue(
            mode="research",
            prompt=question,
            idempotency_key=idempotency_key,
            source_text=result.text,
            source_label=result.path.name,
        )

    def coding_job_list(self, *, limit: int = 20, include_result: bool = False) -> dict[str, Any]:
        self._capabilities().require("coding.read")
        tg_user_id = self._coding_owner_id()
        items = self._coding_jobs().list_for_user(tg_user_id, limit=limit)
        if include_result:
            return {"items": [_value_payload(item) for item in items]}
        return {"items": [_coding_job_summary(item) for item in items]}

    def coding_job_get(self, *, job_id: int) -> dict[str, Any]:
        self._capabilities().require("coding.read")
        return _value_payload(self._coding_jobs().get_for_user(job_id, tg_user_id=self._coding_owner_id()))

    def capability_decision(self, *, capability: str) -> dict[str, Any]:
        return _value_payload(self._capabilities().decide(capability))

    def action_plan_create(
        self, *, actions: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        for action in actions:
            self._capabilities().require(str(action.get("type") or ""))
        plan = self._plans().create(actions, idempotency_key=idempotency_key)
        return _plan_payload(plan)

    def action_plan_approve(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().approve(plan_id))

    def action_plan_execute(self, *, plan_id: int, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Plan execution требует подтверждение пользователя.")
        store = self._plans()
        for action in store.get(plan_id).actions:
            self._capabilities().require(action.action_type)
        if store.get(plan_id).status == "draft":
            store.approve(plan_id)
        return _plan_payload(execute_plan(store, plan_id, self._action_adapter()))

    def action_plan_cancel(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().cancel(plan_id))

    def action_plan_get(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().get(plan_id))

    def action_plan_trace(self, *, plan_id: int) -> dict[str, Any]:
        return self._plans().compact_trace(plan_id)

    def action_plan_pause(self, *, plan_id: int) -> dict[str, Any]:
        self._capabilities().require("planner.control")
        return _plan_payload(self._plans().pause(plan_id))

    def action_plan_resume(self, *, plan_id: int) -> dict[str, Any]:
        self._capabilities().require("planner.control")
        return _plan_payload(self._plans().resume(plan_id))

    async def action_plan_confirm_execute(
        self,
        *,
        actions: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        for action in actions:
            self._capabilities().require(str(action.get("type") or ""))
        store = self._plans()
        plan = store.create(actions, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            if not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        completed = execute_plan(store, plan.id, self._action_adapter())
        await self._deliver_plan_receipt(completed)
        return _plan_payload(completed)

    async def _deliver_plan_receipt(self, plan: ActionPlan) -> None:
        """Send one durable success receipt when Telegram transport drops the tool turn."""
        enabled = os.getenv("HERMES_ACTION_PLAN_RECEIPT_DELIVERY", "true").strip().casefold()
        if enabled not in {"1", "true", "yes", "on"} or plan.status != "succeeded":
            return
        try:
            chat_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
        except ValueError:
            return
        if chat_id <= 0:
            return
        sender = self.plan_receipt_sender or HermesTelegramSender()
        summary = f"Готово: план #{plan.id} выполнен."
        try:
            await asyncio.to_thread(sender, chat_id, summary)
        except Exception as error:
            # The plan has already completed durably. A gateway final response
            # may still arrive, so a receipt delivery failure must not undo it.
            logger.warning("Could not deliver action-plan receipt %s: %s", plan.id, error)

    async def action_plan_dag_confirm_execute(
        self,
        *,
        nodes: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        for node in nodes:
            self._capabilities().require(str(node.get("type") or ""))
        store = self._plans()
        plan = store.create_dag(nodes, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            if not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        return _plan_payload(execute_plan(store, plan.id, self._action_adapter()))

    def telegram_text_export(
        self,
        *,
        peer: str,
        output_format: str = "txt",
        limit: int = 5000,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Экспорт требует одно явное подтверждение пользователя.")
        result = self.exporter(peer=peer, output_format=output_format, limit=limit)
        return {
            "path": str(result.path),
            "peer": result.peer,
            "title": result.title,
            "message_count": result.message_count,
            "output_format": result.output_format,
            "truncated": result.truncated,
            "expires_at": result.expires_at.isoformat(),
            "attachment": _document_attachment(result.path),
        }

    async def telegram_text_export_confirmed(
        self,
        *,
        peer: str,
        output_format: str = "txt",
        limit: int = 5000,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        self._capabilities().require("telegram.export")
        preview = f"Экспортировать текст Telegram peer {peer}: до {limit} сообщений, формат {output_format}."
        if not await confirmer(preview):
            return {"status": "cancelled"}
        return await asyncio.to_thread(
            self.telegram_text_export,
            peer=peer,
            output_format=output_format,
            limit=limit,
            confirmed=True,
        )

    def telegram_file_download(
        self,
        *,
        peer: str,
        file_limit: int = 5,
        scan_limit: int = 500,
        message_ids: list[int] | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Загрузка файлов требует одно явное подтверждение пользователя.")
        result = self.file_downloader(
            peer=peer,
            file_limit=file_limit,
            scan_limit=scan_limit,
            message_ids=message_ids,
        )
        return {
            "status": "ok",
            "peer": result.peer,
            "title": result.title,
            "items": [
                {
                    "message_id": item.message_id,
                    "name": item.name,
                    "size_bytes": item.size_bytes,
                    "mime_type": item.mime_type,
                    "attachment": _document_attachment(item.path),
                }
                for item in result.items
            ],
            "skipped_oversized": result.skipped_oversized,
            "expires_at": result.expires_at.isoformat(),
        }

    async def telegram_file_download_confirmed(
        self,
        *,
        peer: str,
        file_limit: int = 5,
        scan_limit: int = 500,
        message_ids: list[int] | None = None,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        self._capabilities().require("telegram.export")
        preview = (
            f"Скачать из Telegram peer {peer}: до {file_limit} файлов, "
            f"просмотреть до {scan_limit} сообщений, максимум 20 МБ на файл."
        )
        if not await confirmer(preview):
            return {"status": "cancelled"}
        return await asyncio.to_thread(
            self.telegram_file_download,
            peer=peer,
            file_limit=file_limit,
            scan_limit=scan_limit,
            message_ids=message_ids,
            confirmed=True,
        )

    def _plans(self) -> ActionPlanStore:
        return self._store("plans", lambda: ActionPlanStore(self.database_path))

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
        return self._store("coding_jobs", lambda: NativeCodingJobStore(self.database_path))

    def _voice_vocabulary(self) -> VoiceVocabularyStore:
        return self._store("voice_vocabulary", lambda: VoiceVocabularyStore(self.database_path))

    def _coding_owner_id(self) -> int:
        tg_user_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
        if tg_user_id <= 0:
            raise RuntimeError("HERMES_OWNER_TELEGRAM_CHAT_ID is required")
        return tg_user_id

    def _sync_subscriptions(self) -> None:
        if self.subscription_sync is None:
            return
        try:
            rows = [_value_payload(item) for item in self._subscriptions().list()]
            self.subscription_sync(rows)
        except Exception:
            logger.exception("Optional subscription sync failed")

    def _capabilities(self) -> CapabilityPolicyStore:
        return self._store("capabilities", lambda: CapabilityPolicyStore(self.database_path))

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


def _plan_payload(plan: ActionPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "status": plan.status,
        "idempotency_key": plan.idempotency_key,
        "actions": [asdict(action) for action in plan.actions],
    }


def _plan_preview(plan: ActionPlan) -> str:
    rows = []
    for action in plan.actions:
        title = str(action.payload.get("title") or action.payload.get("subject") or "без названия")
        rows.append(f"{action.position + 1}. {action.action_type}: {title}")
    return "\n".join(rows)


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
                result = getattr(self._task_calendar, handlers[str(action["type"])])(**action["payload"])
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


def _message_plan_payload(plan: MessagePlan) -> dict[str, Any]:
    return _value_payload(plan)


def _memory_is_stale(value: str, *, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < now - timedelta(days=90)


def _document_attachment(path: Path) -> dict[str, str]:
    value = str(path)
    return {
        "path": value,
        "directive": f"[[as_document]]\nMEDIA:{value}",
    }


def _message_plan_preview(plan: MessagePlan) -> str:
    return "\n".join(
        f"{index}. {item.contact_name}: {item.text} ({item.send_at.isoformat()})"
        for index, item in enumerate(plan.messages, start=1)
    )


def _monitor_payload(monitor: Monitor) -> dict[str, Any]:
    return _value_payload(monitor)


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


def _coding_job_summary(job: Any) -> dict[str, Any]:
    """Keep routine status checks small; the full report is fetched explicitly."""
    return {
        "id": job.id,
        "mode": job.mode,
        "prompt": _short_text(job.prompt, 180),
        "repository_url": job.repository_url,
        "source_label": job.source_label,
        "status": job.status,
        "result_summary": _short_text(job.result_text, 160),
        "last_error": _short_text(job.last_error, 160),
        "delivery_status": job.delivery_status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _short_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else f"{clean[:limit - 1].rstrip()}…"
