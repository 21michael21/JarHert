from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable

from .action_plans import ActionPlan, ActionPlanStore, execute_plan
from .task_calendar import TaskCalendarAdapter
from .telegram_text_export import ExportResult, run_telegram_export


AdapterFactory = Callable[[], Any]
Exporter = Callable[..., ExportResult]
Confirmer = Callable[[str], Awaitable[bool]]


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
    ) -> None:
        self.database_path = Path(database_path or personal_os_database_path()).expanduser()
        self.adapter_factory = adapter_factory
        self.exporter = exporter

    def integration_health(self) -> dict[str, bool]:
        health = self.adapter_factory().health_check()
        return {
            "ok": bool(health.ok),
            "trello_ok": bool(health.trello_ok),
            "calendar_ok": bool(health.calendar_ok),
        }

    def task_list(self, *, list_name: str | None = None) -> dict[str, str]:
        return {"items": self.adapter_factory().list_tasks(list_name=list_name)}

    def calendar_list(self, *, when: str = "today") -> dict[str, str]:
        return {"items": self.adapter_factory().list_calendar_events(when=when)}

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
        return _plan_payload(execute_plan(store, plan_id, self.adapter_factory()))

    def action_plan_cancel(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().cancel(plan_id))

    async def action_plan_confirm_execute(
        self,
        *,
        actions: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        store = self._plans()
        plan = store.create(actions, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            if not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        return _plan_payload(execute_plan(store, plan.id, self.adapter_factory()))

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
        preview = f"Экспортировать текст Telegram peer {peer}: до {limit} сообщений, формат {output_format}."
        if not await confirmer(preview):
            return {"status": "cancelled"}
        return self.telegram_text_export(
            peer=peer,
            output_format=output_format,
            limit=limit,
            confirmed=True,
        )

    def _plans(self) -> ActionPlanStore:
        return ActionPlanStore(self.database_path)


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
        title = str(action.payload.get("title") or "без названия")
        rows.append(f"{action.position + 1}. {action.action_type}: {title}")
    return "\n".join(rows)
