from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PersonalRhythmStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def claim_summary(self, *, summary_type: str, period_key: str) -> dict[str, str] | None:
        kind = _summary_type(summary_type)
        key = _required(period_key, "Period key", limit=40)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM personal_summary_deliveries WHERE summary_type = ? AND period_key = ?",
                (kind, key),
            ).fetchone()
            if row is not None and row["status"] == "sent":
                connection.commit()
                return {"status": "already_sent", "external_id": str(row["external_id"] or "")}
            if row is None:
                connection.execute(
                    """
                    INSERT INTO personal_summary_deliveries(summary_type, period_key, status)
                    VALUES (?, ?, 'sending')
                    """,
                    (kind, key),
                )
            else:
                connection.execute(
                    """
                    UPDATE personal_summary_deliveries
                    SET status = 'sending', last_error = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE summary_type = ? AND period_key = ?
                    """,
                    (kind, key),
                )
            connection.commit()
        return None

    def finish_summary(
        self,
        *,
        summary_type: str,
        period_key: str,
        external_id: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE personal_summary_deliveries
                SET status = 'sent', external_id = ?, sent_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE summary_type = ? AND period_key = ? AND status = 'sending'
                """,
                (external_id, _summary_type(summary_type), _required(period_key, "Period key", limit=40)),
            )

    def fail_summary(self, *, summary_type: str, period_key: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE personal_summary_deliveries
                SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE summary_type = ? AND period_key = ? AND status = 'sending'
                """,
                (
                    str(error or "delivery failed")[:500],
                    _summary_type(summary_type),
                    _required(period_key, "Period key", limit=40),
                ),
            )

    def weekly_review(self, *, now: str | None = None, timezone_name: str = "Europe/Moscow") -> dict[str, Any]:
        current = _current_time(now, timezone_name)
        week_start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        range_start = week_start.astimezone(timezone.utc)
        range_end = current.astimezone(timezone.utc) + timedelta(seconds=1)
        completed: list[dict[str, Any]] = []
        moved: list[dict[str, Any]] = []
        stuck: list[dict[str, Any]] = []
        with self._connect() as connection:
            if _table_exists(connection, "plan_actions"):
                rows = connection.execute(
                    """
                    SELECT a.id, a.action_type, a.payload_json, a.status, a.error,
                           p.finished_at
                    FROM plan_actions a JOIN action_plans p ON p.id = a.plan_id
                    WHERE p.finished_at IS NOT NULL
                    ORDER BY a.id
                    """
                ).fetchall()
                for row in rows:
                    finished_at = _parse_db_time(str(row["finished_at"]))
                    if not range_start <= finished_at < range_end:
                        continue
                    payload = json.loads(row["payload_json"])
                    item = {
                        "id": int(row["id"]),
                        "title": str(payload.get("title") or payload.get("subject") or "Без названия"),
                        "action_type": str(row["action_type"]),
                    }
                    if row["status"] == "failed":
                        item["error"] = str(row["error"] or "Неизвестная ошибка")[:200]
                        stuck.append(item)
                    elif row["status"] == "succeeded" and row["action_type"] == "task.done":
                        completed.append(item)
                    elif row["status"] == "succeeded" and row["action_type"] == "task.move":
                        item["target"] = str(payload.get("target_list") or "")
                        moved.append(item)

            commitments = []
            if _table_exists(connection, "commitments"):
                commitments = connection.execute(
                    "SELECT * FROM commitments ORDER BY due_at IS NULL, due_at, id"
                ).fetchall()

        now_utc = current.astimezone(timezone.utc)
        next_limit = now_utc + timedelta(days=14)
        completed_commitments: list[dict[str, Any]] = []
        priorities: list[dict[str, Any]] = []
        for row in commitments:
            due_at = _parse_optional_time(row["due_at"])
            completed_at = _parse_optional_time(row["completed_at"])
            item = {"id": int(row["id"]), "title": str(row["subject"]), "due_at": row["due_at"]}
            if row["status"] == "done" and completed_at and range_start <= completed_at < range_end:
                completed_commitments.append(item)
            elif row["status"] == "open" and due_at and due_at < now_utc:
                stuck.append({**item, "action_type": "commitment.overdue", "error": "Просрочено"})
            elif row["status"] == "open" and due_at and now_utc <= due_at <= next_limit:
                priorities.append(item)
        priorities.sort(key=lambda item: (str(item["due_at"]), int(item["id"])))
        result = {
            "completed": completed,
            "completed_commitments": completed_commitments,
            "moved": moved,
            "stuck": stuck,
            "top_three": priorities[:3],
        }
        result["text"] = format_weekly_review(result)
        return result

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS personal_summary_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary_type TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at TEXT,
                    UNIQUE(summary_type, period_key)
                );
                """
            )


def format_daily_brief(data: dict[str, Any]) -> str:
    lines = ["Сегодня:"]
    calendar_items = _external_items(data.get("calendar"), empty_markers=("no events found", "событий нет"))
    if calendar_items:
        lines.append("Календарь:")
        lines.extend(f"• {item}" for item in calendar_items[:3])
        if len(calendar_items) > 3:
            lines.append(f"• ещё {len(calendar_items) - 3}")
    elif data.get("calendar"):
        lines.append("Календарь: пусто")

    task_items = _external_items(data.get("tasks"))
    if task_items:
        lines.append("Задачи:")
        lines.extend(f"{index}. {item}" for index, item in enumerate(task_items[:3], start=1))
        if len(task_items) > 3:
            lines.append(f"…и ещё {len(task_items) - 3}")
    reminders = data.get("reminders") or []
    if reminders:
        lines.append("Напоминания: " + "; ".join(str(item["text"]) for item in reminders[:3]))
    priorities = data.get("top_three") or []
    if priorities:
        lines.append("Главное: " + "; ".join(str(item["title"]) for item in priorities[:3]))
    if len(lines) == 1:
        lines.append("План пуст. Можно спокойно выбрать одну главную задачу.")
    return "\n".join(lines)


def format_weekly_review(data: dict[str, Any]) -> str:
    completed_count = len(data.get("completed") or []) + len(data.get("completed_commitments") or [])
    lines = [
        "За неделю:",
        f"Готово: {completed_count}. Перенесено: {len(data.get('moved') or [])}. Зависло: {len(data.get('stuck') or [])}.",
    ]
    priorities = data.get("top_three") or []
    if priorities:
        lines.append("Следующие три: " + "; ".join(str(item["title"]) for item in priorities))
    else:
        lines.append("На следующую неделю явных приоритетов пока нет.")
    return "\n".join(lines)


def dispatch_personal_summary(
    store: PersonalRhythmStore,
    build_text: Callable[[], str],
    sender: Callable[[int, str], str | None],
    *,
    chat_id: int,
    summary_type: str,
    period_key: str,
) -> dict[str, str]:
    existing = store.claim_summary(summary_type=summary_type, period_key=period_key)
    if existing is not None:
        return existing
    try:
        external_id = sender(int(chat_id), _required(build_text(), "Summary", limit=4000))
    except Exception as error:
        store.fail_summary(summary_type=summary_type, period_key=period_key, error=str(error))
        raise
    store.finish_summary(
        summary_type=summary_type,
        period_key=period_key,
        external_id=external_id,
    )
    return {"status": "sent", "external_id": str(external_id or "")}


def _current_time(value: str | None, timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError("Неизвестный timezone.") from error
    if value is None:
        return datetime.now(zone)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Current time должен содержать timezone.")
    return parsed.astimezone(zone)


def _parse_db_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _parse_optional_time(value: Any) -> datetime | None:
    return _parse_db_time(str(value)) if value else None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _compact(value: Any) -> str:
    return " ".join(str(value).split())[:500]


def _external_items(value: Any, *, empty_markers: tuple[str, ...] = ()) -> list[str]:
    """Turn an external CLI listing into short, chat-readable titles."""
    raw = str(value or "").strip()
    empty_value = raw.casefold().rstrip(".!")
    if not raw or empty_value in {marker.casefold().rstrip(".!") for marker in empty_markers}:
        return []
    chunks = re.split(r"(?:^|\n)\s*-\s+|\s+-\s+(?=[^|\n]{1,180}\s+\[)", raw)
    items: list[str] = []
    for chunk in chunks:
        title = re.sub(r"https?://\S+", "", chunk).split("|", 1)[0]
        title = re.sub(r"\s*\[[^\]]+\]\s*$", "", title).strip(" -•\t\n")
        title = " ".join(title.split())
        if title:
            items.append(title[:140].rstrip())
    return items or [_compact(raw)[:140].rstrip()]


def _summary_type(value: str) -> str:
    clean = str(value or "").strip().casefold()
    if clean not in {"daily", "weekly"}:
        raise ValueError("Summary type должен быть daily или weekly.")
    return clean


def _required(value: str, label: str, *, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean
