"""Preview builders and validators for dashboard action routes."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from .knowledge_archive import validate_archive_url


def coding_mode(value: Any) -> str:
    mode = str(value or "coding").strip().casefold()
    if mode not in {"coding", "research"}:
        raise HTTPException(status_code=422, detail="unsupported coding mode")
    return mode


def coding_context(payload: dict[str, Any], *, mode: str) -> tuple[str | None, list[str]]:
    if mode == "coding":
        return github_repository_url(payload.get("repository_url")), []
    return None, research_source_urls(payload.get("source_urls"))


def coding_preview(*, mode: str, repository_url: str | None, source_urls: list[str]) -> list[str]:
    if mode == "research":
        return [
            "Проверить гипотезу по источникам",
            f"Источники: {len(source_urls)}",
            "Runner работает в sandbox; внешние действия только после явного подтверждения.",
        ]
    return [
        "Поставить кодовую задачу в очередь",
        f"Репозиторий: {repository_url}",
        "Runner может подготовить ветку и commit; push/deploy только после отдельного подтверждения.",
    ]


def plan_preview(plan: dict[str, Any]) -> list[str]:
    labels = {
        "task.create": "Создать задачу",
        "task.move": "Переместить задачу",
        "task.priority": "Изменить приоритет",
        "task.done": "Закрыть задачу",
        "task.delete": "Удалить задачу",
        "calendar.create": "Создать событие",
        "calendar.move": "Перенести событие",
        "calendar.delete": "Удалить событие",
        "reminder.create": "Создать напоминание",
        "note.save": "Сохранить заметку",
        "commitment.create": "Сохранить обещание",
    }
    rows: list[str] = []
    for action in list(plan.get("actions") or []):
        if not isinstance(action, dict):
            continue
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        title = str(payload.get("title") or payload.get("text") or payload.get("subject") or "без названия")
        rows.append(f"{labels.get(str(action.get('action_type') or action.get('type') or ''), 'Выполнить')}: {title}")
    return rows


def github_repository_url(value: Any) -> str:
    raw = _required_text(value, label="GitHub репозиторий", limit=500)
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
    ):
        raise HTTPException(status_code=422, detail="GitHub репозиторий должен быть HTTPS URL вида owner/repo")
    return f"https://github.com/{parts[0]}/{parts[1]}"


def research_source_urls(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 10:
        raise HTTPException(status_code=422, detail="Добавь от 1 до 10 HTTPS ссылок для проверки")
    urls: list[str] = []
    for item in value:
        try:
            url = validate_archive_url(str(item))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if url not in urls:
            urls.append(url)
    if not urls:
        raise HTTPException(status_code=422, detail="Добавь HTTPS ссылку для проверки")
    return urls


def _required_text(value: Any, *, label: str, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > limit:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    return clean
