from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from native_tools.contacts import ContactStore, ContactStoreError
    from native_tools.delivery import HermesTelegramSender, dispatch_due_messages
    from native_tools.events import EventStore
    from native_tools.monitors import MonitorRegistry, MonitorRunner
    from native_tools.skill_distillation import SkillDistiller
    from native_tools.sandbox_worker import SandboxTask, SandboxedHermesWorker
    from native_tools.action_plans import ActionPlanError, ActionPlanStore, execute_plan
    from native_tools.task_calendar import TaskCalendarAdapter, TaskCalendarError
    from native_tools.telegram_text_export import (
        TelegramExportError,
        run_telegram_export,
        telegram_session_status,
    )
else:
    from .contacts import ContactStore, ContactStoreError
    from .delivery import HermesTelegramSender, dispatch_due_messages
    from .events import EventStore
    from .monitors import MonitorRegistry, MonitorRunner
    from .skill_distillation import SkillDistiller
    from .sandbox_worker import SandboxTask, SandboxedHermesWorker
    from .action_plans import ActionPlanError, ActionPlanStore, execute_plan
    from .task_calendar import TaskCalendarAdapter, TaskCalendarError
    from .telegram_text_export import (
        TelegramExportError,
        run_telegram_export,
        telegram_session_status,
    )


def database_path() -> Path:
    explicit = os.getenv("PERSONAL_OS_DB", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    return home / "data" / "personal-os.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-os")
    parser.add_argument("--db", type=Path, default=database_path())
    commands = parser.add_subparsers(dest="command", required=True)

    contact = commands.add_parser("contact")
    contact_commands = contact.add_subparsers(dest="contact_command", required=True)
    contact_add = contact_commands.add_parser("add")
    contact_add.add_argument("--name", required=True)
    contact_add.add_argument("--telegram-chat-id", type=int, required=True)
    contact_add.add_argument("--alias", action="append", default=[])
    contact_commands.add_parser("list")

    message = commands.add_parser("message")
    message_commands = message.add_subparsers(dest="message_command", required=True)
    plan = message_commands.add_parser("plan")
    plan.add_argument("--items-json", required=True)
    plan.add_argument("--idempotency-key", required=True)
    approve = message_commands.add_parser("approve")
    approve.add_argument("plan_id", type=int)
    show = message_commands.add_parser("show")
    show.add_argument("plan_id", type=int)

    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--limit", type=int, default=20)
    dispatch.add_argument("--now")

    monitor = commands.add_parser("monitor")
    monitor_commands = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_add = monitor_commands.add_parser("add")
    monitor_add.add_argument("--name", required=True)
    monitor_add.add_argument("--source-type", choices=["github_releases"], required=True)
    monitor_add.add_argument("--source-config-json", required=True)
    monitor_add.add_argument("--condition", required=True)
    monitor_commands.add_parser("list")
    monitor_remove = monitor_commands.add_parser("remove")
    monitor_remove.add_argument("monitor_id", type=int)
    monitor_commands.add_parser("check")

    skill = commands.add_parser("skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_observe = skill_commands.add_parser("observe")
    skill_observe.add_argument("--workflow-key", required=True)
    skill_observe.add_argument("--title", required=True)
    skill_observe.add_argument("--steps-json", required=True)
    skill_observe.add_argument("--idempotency-key", required=True)
    skill_observe.add_argument("--success", action="store_true")
    skill_observe.add_argument("--confirmed", action="store_true")
    skill_list = skill_commands.add_parser("list")
    skill_list.add_argument("--ready-only", action="store_true")
    skill_show = skill_commands.add_parser("show")
    skill_show.add_argument("workflow_key")
    skill_staged = skill_commands.add_parser("mark-staged")
    skill_staged.add_argument("workflow_key")

    sandbox = commands.add_parser("sandbox")
    sandbox_commands = sandbox.add_subparsers(dest="sandbox_command", required=True)
    sandbox_run = sandbox_commands.add_parser("run")
    sandbox_run.add_argument("--mode", choices=["coding", "research"], required=True)
    sandbox_run.add_argument("--prompt", required=True)
    sandbox_run.add_argument("--repository-url")
    sandbox_run.add_argument("--source-url", action="append", default=[])

    task = commands.add_parser("task")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_create = task_commands.add_parser("create")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--list-name", default="Inbox")
    task_create.add_argument("--project")
    task_create.add_argument("--priority")
    task_create.add_argument("--due")
    task_create.add_argument("--description")
    task_create.add_argument("--confirmed", action="store_true")
    task_list = task_commands.add_parser("list")
    task_list.add_argument("--list-name")
    task_move = task_commands.add_parser("move")
    task_move.add_argument("--title", required=True)
    task_move.add_argument("--target-list", required=True)
    task_move.add_argument("--confirmed", action="store_true")
    task_done = task_commands.add_parser("done")
    task_done.add_argument("--title", required=True)
    task_done.add_argument("--summary", default="Готово.")
    task_done.add_argument("--confirmed", action="store_true")
    task_delete = task_commands.add_parser("delete")
    task_delete.add_argument("--title", required=True)
    task_delete.add_argument("--confirmed", action="store_true")

    calendar = commands.add_parser("calendar")
    calendar_commands = calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_create = calendar_commands.add_parser("create")
    calendar_create.add_argument("--title", required=True)
    calendar_create.add_argument("--start", required=True)
    calendar_create.add_argument("--end", required=True)
    calendar_create.add_argument("--reminder-minutes", type=int)
    calendar_create.add_argument("--description")
    calendar_create.add_argument("--confirmed", action="store_true")
    calendar_list = calendar_commands.add_parser("list")
    calendar_list.add_argument("--when", default="today")
    calendar_move = calendar_commands.add_parser("move")
    calendar_move.add_argument("--title", required=True)
    calendar_move.add_argument("--start", required=True)
    calendar_move.add_argument("--end", required=True)
    calendar_move.add_argument("--confirmed", action="store_true")
    calendar_delete = calendar_commands.add_parser("delete")
    calendar_delete.add_argument("--title", required=True)
    calendar_delete.add_argument("--confirmed", action="store_true")

    plan = commands.add_parser("plan")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    plan_create = plan_commands.add_parser("create")
    plan_create.add_argument("--actions-json", required=True)
    plan_create.add_argument("--idempotency-key", required=True)
    for name in ("show", "approve", "execute", "cancel"):
        command = plan_commands.add_parser(name)
        command.add_argument("plan_id", type=int)

    commands.add_parser("integration-health")

    chat = commands.add_parser("chat")
    chat_commands = chat.add_subparsers(dest="chat_command", required=True)
    chat_commands.add_parser("session-status")
    chat_export = chat_commands.add_parser("export")
    chat_export.add_argument("--peer", required=True)
    chat_export.add_argument("--format", choices=["txt", "jsonl"], default="txt")
    chat_export.add_argument("--limit", type=int, default=5000)
    chat_export.add_argument("--confirmed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_command(args)
    except (
        ActionPlanError,
        ContactStoreError,
        TaskCalendarError,
        TelegramExportError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "result": _json_value(payload)}, ensure_ascii=False, separators=(",", ":")))
    return 0


def run_command(args: argparse.Namespace) -> Any:
    store = ContactStore(args.db)
    if args.command == "contact" and args.contact_command == "add":
        return store.add_contact(
            name=args.name,
            telegram_chat_id=args.telegram_chat_id,
            aliases=args.alias,
        )
    if args.command == "contact" and args.contact_command == "list":
        return store.list_contacts()
    if args.command == "message" and args.message_command == "plan":
        items = json.loads(args.items_json)
        if not isinstance(items, list):
            raise ValueError("items-json должен быть JSON-массивом.")
        return store.create_message_plan(items, idempotency_key=args.idempotency_key)
    if args.command == "message" and args.message_command == "approve":
        return store.approve_message_plan(args.plan_id)
    if args.command == "message" and args.message_command == "show":
        return store.get_message_plan(args.plan_id)
    if args.command == "dispatch":
        now = datetime.fromisoformat(args.now) if args.now else None
        return dispatch_due_messages(store, HermesTelegramSender(), now=now, limit=args.limit)
    if args.command == "monitor":
        registry = MonitorRegistry(args.db)
        if args.monitor_command == "add":
            config = json.loads(args.source_config_json)
            if not isinstance(config, dict):
                raise ValueError("source-config-json должен быть JSON-объектом.")
            return registry.add(
                name=args.name,
                source_type=args.source_type,
                source_config=config,
                condition=args.condition,
            )
        if args.monitor_command == "list":
            return registry.list()
        if args.monitor_command == "remove":
            return registry.disable(args.monitor_id)
        if args.monitor_command == "check":
            return MonitorRunner(registry, EventStore(args.db)).run_once()
    if args.command == "skill":
        distiller = SkillDistiller(args.db)
        if args.skill_command == "observe":
            steps = json.loads(args.steps_json)
            if not isinstance(steps, list):
                raise ValueError("steps-json должен быть JSON-массивом.")
            return distiller.observe(
                workflow_key=args.workflow_key,
                title=args.title,
                steps=steps,
                idempotency_key=args.idempotency_key,
                success=args.success,
                confirmed=args.confirmed,
            )
        if args.skill_command == "list":
            return distiller.list_candidates(ready_only=args.ready_only)
        if args.skill_command == "show":
            return distiller.get_candidate(args.workflow_key)
        if args.skill_command == "mark-staged":
            return distiller.mark_staged(args.workflow_key)
    if args.command == "sandbox" and args.sandbox_command == "run":
        worker = SandboxedHermesWorker(
            profile_binary=os.getenv("HERMES_PROFILE_BIN", "jarhert")
        )
        return worker.run(
            SandboxTask(
                mode=args.mode,
                prompt=args.prompt,
                repository_url=args.repository_url,
                source_urls=tuple(args.source_url),
            )
        )
    if args.command in {"task", "calendar", "integration-health"}:
        adapter = TaskCalendarAdapter.from_env()
        if args.command == "integration-health":
            return adapter.health_check()
        if args.command == "task" and args.task_command == "list":
            return adapter.list_tasks(list_name=args.list_name)
        if args.command == "calendar" and args.calendar_command == "list":
            return adapter.list_calendar_events(when=args.when)
        _confirmed(args)
        if args.command == "task" and args.task_command == "create":
            return adapter.create_task(
                title=args.title,
                list_name=args.list_name,
                project=args.project,
                priority=args.priority,
                due=args.due,
                description=args.description,
            )
        if args.command == "task" and args.task_command == "move":
            return adapter.move_task(title=args.title, target_list=args.target_list)
        if args.command == "task" and args.task_command == "done":
            return adapter.complete_task(title=args.title, summary=args.summary)
        if args.command == "task" and args.task_command == "delete":
            return adapter.delete_task(title=args.title)
        if args.command == "calendar" and args.calendar_command == "create":
            return adapter.create_calendar_event(
                title=args.title,
                start=args.start,
                end=args.end,
                reminder_minutes=args.reminder_minutes,
                description=args.description,
            )
        if args.command == "calendar" and args.calendar_command == "move":
            return adapter.move_calendar_event(title=args.title, start=args.start, end=args.end)
        if args.command == "calendar" and args.calendar_command == "delete":
            return adapter.delete_calendar_event(title=args.title)
    if args.command == "plan":
        plans = ActionPlanStore(args.db)
        if args.plan_command == "create":
            actions = json.loads(args.actions_json)
            if not isinstance(actions, list):
                raise ValueError("actions-json должен быть JSON-массивом.")
            return plans.create(actions, idempotency_key=args.idempotency_key)
        if args.plan_command == "show":
            return plans.get(args.plan_id)
        if args.plan_command == "approve":
            return plans.approve(args.plan_id)
        if args.plan_command == "cancel":
            return plans.cancel(args.plan_id)
        if args.plan_command == "execute":
            return execute_plan(plans, args.plan_id, TaskCalendarAdapter.from_env())
    if args.command == "chat":
        if args.chat_command == "session-status":
            return telegram_session_status()
        if args.chat_command == "export":
            _confirmed(args)
            return run_telegram_export(peer=args.peer, output_format=args.format, limit=args.limit)
    raise ValueError("Неизвестная команда.")


def _confirmed(args: argparse.Namespace) -> None:
    if not getattr(args, "confirmed", False):
        raise ValueError("Mutation требует --confirmed или единый approved plan.")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    return value


if __name__ == "__main__":
    raise SystemExit(main())
