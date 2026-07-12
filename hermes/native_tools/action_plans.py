from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ActionPlanError(RuntimeError):
    pass


ACTION_SCHEMAS: dict[str, tuple[str, ...]] = {
    "note.save": ("subject", "content"),
    "commitment.create": ("subject", "content"),
    "reminder.create": ("text", "remind_at"),
    "task.create": ("title",),
    "task.move": ("title", "target_list"),
    "task.done": ("title",),
    "task.delete": ("title",),
    "calendar.create": ("title", "start", "end"),
    "calendar.move": ("title", "start", "end"),
    "calendar.delete": ("title",),
}
EXTERNAL_ACTIONS = frozenset(action for action in ACTION_SCHEMAS if action.startswith(("task.", "calendar.")))


@dataclass(frozen=True)
class PlannedAction:
    id: int
    position: int
    node_key: str
    action_type: str
    payload: dict[str, Any]
    depends_on_action_ids: tuple[int, ...]
    status: str
    result: str | None
    result_meta: dict[str, str]
    error: str | None


@dataclass(frozen=True)
class ActionPlan:
    id: int
    status: str
    idempotency_key: str
    actions: tuple[PlannedAction, ...]


class ActionPlanStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(self, actions: list[dict[str, Any]], *, idempotency_key: str) -> ActionPlan:
        key = _required(idempotency_key, "idempotency_key")[:180]
        if not 1 <= len(actions) <= 20:
            raise ActionPlanError("Plan должен содержать от 1 до 20 actions.")
        normalized = [_validate_action(item) for item in actions]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM action_plans WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._get(connection, int(existing["id"]))
            plan_id = int(
                connection.execute(
                    "INSERT INTO action_plans(status, idempotency_key) VALUES ('draft', ?)",
                    (key,),
                ).lastrowid
            )
            for position, item in enumerate(normalized):
                payload = dict(item["payload"])
                if item["type"] in {"commitment.create", "reminder.create"}:
                    payload.setdefault("idempotency_key", f"{key}:action:{position}")
                connection.execute(
                    """
                    INSERT INTO plan_actions(plan_id, position, node_key, action_type, payload_json, depends_on_json, status)
                    VALUES (?, ?, ?, ?, ?, '[]', 'pending')
                    """,
                    (plan_id, position, f"action-{position + 1}", item["type"], _json(payload)),
                )
            connection.commit()
            return self._get(connection, plan_id)

    def create_dag(self, nodes: list[dict[str, Any]], *, idempotency_key: str) -> ActionPlan:
        """Create an ordered dependency graph without changing flat-plan semantics."""
        key = _required(idempotency_key, "idempotency_key")[:180]
        if not 1 <= len(nodes) <= 20:
            raise ActionPlanError("Plan должен содержать от 1 до 20 nodes.")
        normalized = _validate_nodes(nodes)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM action_plans WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._get(connection, int(existing["id"]))
            plan_id = int(
                connection.execute(
                    "INSERT INTO action_plans(status, idempotency_key) VALUES ('draft', ?)", (key,)
                ).lastrowid
            )
            action_ids: dict[str, int] = {}
            for position, item in enumerate(normalized):
                payload = dict(item["payload"])
                if item["type"] in {"commitment.create", "reminder.create"}:
                    payload.setdefault("idempotency_key", f"{key}:node:{item['key']}")
                action_id = int(
                    connection.execute(
                        """
                        INSERT INTO plan_actions(plan_id, position, node_key, action_type, payload_json, depends_on_json, status)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending')
                        """,
                        (
                            plan_id,
                            position,
                            item["key"],
                            item["type"],
                            _json(payload),
                            _json([action_ids[parent] for parent in item["depends_on"]]),
                        ),
                    ).lastrowid
                )
                action_ids[item["key"]] = action_id
            connection.commit()
            return self._get(connection, plan_id)

    def get(self, plan_id: int) -> ActionPlan:
        with self._connect() as connection:
            return self._get(connection, plan_id)

    def approve(self, plan_id: int) -> ActionPlan:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM action_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise ActionPlanError("Plan не найден.")
            if row["status"] == "draft":
                connection.execute(
                    "UPDATE action_plans SET status = 'approved', approved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (plan_id,),
                )
            elif row["status"] != "approved":
                raise ActionPlanError(f"Plan нельзя подтвердить в статусе {row['status']}.")
            connection.commit()
            return self._get(connection, plan_id)

    def cancel(self, plan_id: int) -> ActionPlan:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE action_plans SET status = 'cancelled' WHERE id = ? AND status = 'draft'",
                (plan_id,),
            )
            if cursor.rowcount != 1:
                raise ActionPlanError("Можно отменить только draft plan.")
        return self.get(plan_id)

    def pause(self, plan_id: int) -> ActionPlan:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE action_plans SET status = 'paused' WHERE id = ? AND status IN ('draft', 'approved')",
                (plan_id,),
            )
            if cursor.rowcount != 1:
                raise ActionPlanError("Можно приостановить только draft или approved plan.")
        return self.get(plan_id)

    def resume(self, plan_id: int) -> ActionPlan:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE action_plans SET status = 'approved' WHERE id = ? AND status = 'paused'",
                (plan_id,),
            )
            if cursor.rowcount != 1:
                raise ActionPlanError("Можно продолжить только paused plan.")
        return self.get(plan_id)

    def mark_running(self, action_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE plan_actions SET status = 'running' WHERE id = ? AND status = 'pending'",
                (action_id,),
            )

    def mark_succeeded(self, action_id: int, result: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE plan_actions SET status = 'succeeded', result = ?, result_meta_json = ?, error = NULL
                WHERE id = ? AND status = 'running'
                """,
                (_bounded(result, 3000), _json(_extract_result_meta(result)), action_id),
            )

    def mark_failed(self, action_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE plan_actions SET status = 'failed', error = ? WHERE id = ? AND status = 'running'",
                (_bounded(error, 500), action_id),
            )

    def mark_blocked(self, action_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE plan_actions SET status = 'failed', error = ? WHERE id = ? AND status = 'pending'",
                (_bounded(error, 500), action_id),
            )

    def finish(self, plan_id: int) -> ActionPlan:
        plan = self.get(plan_id)
        statuses = {action.status for action in plan.actions}
        final = "succeeded" if statuses == {"succeeded"} else "failed" if statuses == {"failed"} else "partial"
        with self._connect() as connection:
            connection.execute(
                "UPDATE action_plans SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (final, plan_id),
            )
        return self.get(plan_id)

    def _get(self, connection: sqlite3.Connection, plan_id: int) -> ActionPlan:
        row = connection.execute("SELECT * FROM action_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ActionPlanError("Plan не найден.")
        actions = connection.execute(
            "SELECT * FROM plan_actions WHERE plan_id = ? ORDER BY position",
            (plan_id,),
        ).fetchall()
        return ActionPlan(
            id=int(row["id"]),
            status=row["status"],
            idempotency_key=row["idempotency_key"],
            actions=tuple(_action_from_row(item) for item in actions),
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS action_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    approved_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS plan_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL REFERENCES action_plans(id),
                    position INTEGER NOT NULL,
                    node_key TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    result TEXT,
                    result_meta_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    UNIQUE(plan_id, position)
                );
                """
            )
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(plan_actions)")}
            if "node_key" not in columns:
                connection.execute("ALTER TABLE plan_actions ADD COLUMN node_key TEXT NOT NULL DEFAULT ''")
                connection.execute("UPDATE plan_actions SET node_key = 'action-' || (position + 1) WHERE node_key = ''")
            if "depends_on_json" not in columns:
                connection.execute("ALTER TABLE plan_actions ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def execute_plan(store: ActionPlanStore, plan_id: int, adapter: Any) -> ActionPlan:
    plan = store.get(plan_id)
    if plan.status in {"succeeded", "partial", "failed"}:
        return plan
    if plan.status != "approved":
        raise ActionPlanError("Plan должен быть подтверждён перед выполнением.")
    actions = list(plan.actions)
    while True:
        pending = [action for action in actions if action.status == "pending"]
        if not pending:
            return store.finish(plan_id)
        statuses = {action.id: action.status for action in actions}
        ready = [
            action
            for action in pending
            if all(statuses.get(parent_id) == "succeeded" for parent_id in action.depends_on_action_ids)
        ]
        if not ready:
            for action in pending:
                store.mark_blocked(action.id, "Зависимость не завершилась успешно.")
            return store.finish(plan_id)
        action = ready[0]
        batch_handler = getattr(adapter, "execute_batch", None)
        if action.action_type in EXTERNAL_ACTIONS and callable(batch_handler):
            batch = [item for item in ready if item.action_type in EXTERNAL_ACTIONS]
            for item in batch:
                store.mark_running(item.id)
            try:
                results = batch_handler(
                    [{"type": item.action_type, "payload": item.payload} for item in batch]
                )
                if len(results) != len(batch):
                    raise RuntimeError("Batch adapter вернул неверное число результатов.")
            except Exception as error:
                for item in batch:
                    store.mark_failed(item.id, str(error) or type(error).__name__)
            else:
                for item, result in zip(batch, results, strict=True):
                    if result.get("ok"):
                        store.mark_succeeded(item.id, str(result.get("result") or "Готово."))
                    else:
                        store.mark_failed(item.id, str(result.get("error") or "Batch action failed"))
            actions = list(store.get(plan_id).actions)
            continue
        store.mark_running(action.id)
        try:
            result = _execute_action(adapter, action.action_type, action.payload)
        except Exception as error:
            store.mark_failed(action.id, str(error) or type(error).__name__)
        else:
            store.mark_succeeded(action.id, result)
        actions = list(store.get(plan_id).actions)


def _execute_action(adapter: Any, action_type: str, payload: dict[str, Any]) -> str:
    handlers = {
        "note.save": "save_note",
        "commitment.create": "create_commitment",
        "reminder.create": "create_reminder",
        "task.create": "create_task",
        "task.move": "move_task",
        "task.done": "complete_task",
        "task.delete": "delete_task",
        "calendar.create": "create_calendar_event",
        "calendar.move": "move_calendar_event",
        "calendar.delete": "delete_calendar_event",
    }
    return str(getattr(adapter, handlers[action_type])(**payload))


def _validate_action(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ActionPlanError("Каждый action должен быть JSON-объектом.")
    action_type = str(item.get("type") or "")
    if action_type not in ACTION_SCHEMAS:
        raise ActionPlanError(f"Action '{action_type}' отсутствует в plan allowlist.")
    payload = item.get("payload")
    if not isinstance(payload, dict):
        raise ActionPlanError("Action payload должен быть JSON-объектом.")
    for field in ACTION_SCHEMAS[action_type]:
        if not str(payload.get(field) or "").strip():
            raise ActionPlanError(f"Action {action_type} требует поле {field}.")
    return {"type": action_type, "payload": payload}


def _validate_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ActionPlanError("Каждый node должен быть JSON-объектом.")
        key = _required(str(node.get("key") or ""), "node key")
        if key in seen:
            raise ActionPlanError("Node keys должны быть уникальны.")
        dependencies = node.get("depends_on") or []
        if not isinstance(dependencies, list) or any(
            not isinstance(item, str) or item not in seen for item in dependencies
        ):
            raise ActionPlanError("Зависимость node должна ссылаться на предыдущий node.")
        action = _validate_action({"type": node.get("type"), "payload": node.get("payload")})
        normalized.append({"key": key, **action, "depends_on": dependencies})
        seen.add(key)
    return normalized


def _action_from_row(row: sqlite3.Row) -> PlannedAction:
    return PlannedAction(
        id=int(row["id"]), position=int(row["position"]), node_key=str(row["node_key"]),
        action_type=row["action_type"], payload=json.loads(row["payload_json"]),
        depends_on_action_ids=tuple(int(value) for value in json.loads(row["depends_on_json"])),
        status=row["status"], result=row["result"],
        result_meta=json.loads(row["result_meta_json"]), error=row["error"],
    )


def _extract_result_meta(result: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for key in ("trello_card_id", "calendar_event_id"):
        match = re.search(rf"\b{key}\s*[:=]\s*([A-Za-z0-9_-]{{3,128}})", result)
        if match:
            meta[key] = match.group(1)
    url = re.search(r"https://trello\.com/c/[A-Za-z0-9_-]+[^\s]*", result)
    if url:
        meta["trello_card_url"] = url.group(0)
    return meta


def _required(value: str, field: str) -> str:
    clean = value.strip()
    if not clean:
        raise ActionPlanError(f"{field} не должен быть пустым.")
    return clean


def _bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
