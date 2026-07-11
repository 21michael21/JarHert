from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable

from .action_plans import ActionPlan, ActionPlanStore, execute_plan
from .capabilities import CapabilityPolicyStore
from .contacts import ContactStore, MessagePlan
from .monitors import Monitor, MonitorRegistry
from .memory_consolidation import MemoryConsolidator
from .personal_os import PersonalOSStore
from .personal_crm import PersonalCRMStore
from .personal_productivity import PersonalProductivityStore, local_day_bounds
from .personal_rhythms import PersonalRhythmStore, format_daily_brief
from .skill_distillation import SkillDistiller
from .subscriptions import SubscriptionStore, subscription_sync_from_env
from .task_calendar import TaskCalendarAdapter
from .telegram_text_export import ExportResult, run_telegram_export


AdapterFactory = Callable[[], Any]
Exporter = Callable[..., ExportResult]
Confirmer = Callable[[str], Awaitable[bool]]
SubscriptionSync = Callable[[list[dict[str, Any]]], None]
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
        subscription_sync: SubscriptionSync | None = None,
    ) -> None:
        self.database_path = Path(database_path or personal_os_database_path()).expanduser()
        self.adapter_factory = adapter_factory
        self.exporter = exporter
        self.subscription_sync = subscription_sync if subscription_sync is not None else subscription_sync_from_env()

    def integration_health(self) -> dict[str, bool]:
        self._capabilities().require("integration.health")
        health = self.adapter_factory().health_check()
        return {
            "ok": bool(health.ok),
            "trello_ok": bool(health.trello_ok),
            "calendar_ok": bool(health.calendar_ok),
        }

    def task_list(self, *, list_name: str | None = None) -> dict[str, str]:
        self._capabilities().require("task.list")
        return {"items": self.adapter_factory().list_tasks(list_name=list_name)}

    def calendar_list(self, *, when: str = "today") -> dict[str, str]:
        self._capabilities().require("calendar.list")
        return {"items": self.adapter_factory().list_calendar_events(when=when)}

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

    def monitor_digest(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return self._monitors().build_digest()

    def monitor_digest_mark_delivered(self, *, item_ids: list[int]) -> dict[str, int]:
        self._capabilities().require("monitor.write")
        self._monitors().mark_digest_delivered(item_ids)
        return {"delivered": len(set(int(item_id) for item_id in item_ids))}

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
        adapter = self.adapter_factory()
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

    def capability_decision(self, *, capability: str) -> dict[str, Any]:
        return _value_payload(self._capabilities().decide(capability))

    def action_plan_create(
        self, *, actions: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        plan = self._plans().create(actions, idempotency_key=idempotency_key)
        return _plan_payload(plan)

    def action_plan_approve(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().approve(plan_id))

    def action_plan_execute(self, *, plan_id: int, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Plan execution требует подтверждение пользователя.")
        store = self._plans()
        if store.get(plan_id).status == "draft":
            store.approve(plan_id)
        return _plan_payload(execute_plan(store, plan_id, self._action_adapter()))

    def action_plan_cancel(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().cancel(plan_id))

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

    def _plans(self) -> ActionPlanStore:
        return ActionPlanStore(self.database_path)

    def _contacts(self) -> ContactStore:
        return ContactStore(self.database_path)

    def _monitors(self) -> MonitorRegistry:
        return MonitorRegistry(self.database_path)

    def _skills(self) -> SkillDistiller:
        return SkillDistiller(self.database_path)

    def _memory_consolidator(self) -> MemoryConsolidator:
        return MemoryConsolidator(self.database_path)

    def _personal_os(self) -> PersonalOSStore:
        return PersonalOSStore(self.database_path)

    def _productivity(self) -> PersonalProductivityStore:
        return PersonalProductivityStore(self.database_path)

    def _crm(self) -> PersonalCRMStore:
        return PersonalCRMStore(self.database_path)

    def _rhythms(self) -> PersonalRhythmStore:
        return PersonalRhythmStore(self.database_path)

    def _subscriptions(self) -> SubscriptionStore:
        return SubscriptionStore(self.database_path)

    def _sync_subscriptions(self) -> None:
        if self.subscription_sync is None:
            return
        try:
            rows = [_value_payload(item) for item in self._subscriptions().list()]
            self.subscription_sync(rows)
        except Exception:
            logger.exception("Optional subscription sync failed")

    def _capabilities(self) -> CapabilityPolicyStore:
        return CapabilityPolicyStore(self.database_path)

    def _action_adapter(self) -> "_NativeActionAdapter":
        return _NativeActionAdapter(
            self.adapter_factory,
            self._personal_os(),
            self._productivity(),
        )


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
