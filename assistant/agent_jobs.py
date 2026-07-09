from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class AgentJob:
    id: int
    user_id: int
    goal: str
    status: str
    steps: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None


class AgentJobStore(Protocol):
    def create(self, user_id: int, goal: str, steps: list[str]) -> AgentJob:
        ...

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[AgentJob]:
        ...

    def get_for_user(self, user_id: int, job_id: int) -> AgentJob | None:
        ...


class InMemoryAgentJobStore:
    def __init__(self) -> None:
        self._items: list[AgentJob] = []
        self._next_id = 1

    def create(self, user_id: int, goal: str, steps: list[str]) -> AgentJob:
        now = datetime.now(timezone.utc)
        job = AgentJob(
            id=self._next_id,
            user_id=user_id,
            goal=goal.strip(),
            status="queued",
            steps=list(steps),
            created_at=now,
            updated_at=now,
        )
        self._next_id += 1
        self._items.append(job)
        return job

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[AgentJob]:
        items = [item for item in self._items if item.user_id == user_id]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    def get_for_user(self, user_id: int, job_id: int) -> AgentJob | None:
        for item in self._items:
            if item.user_id == user_id and item.id == job_id:
                return item
        return None


def build_agent_plan(goal: str) -> list[str]:
    clean_goal = " ".join((goal or "").strip().split())
    if not clean_goal:
        return []

    explicit_steps = _extract_explicit_steps(clean_goal)
    if explicit_steps:
        return explicit_steps[:8]

    lowered = clean_goal.lower()
    steps: list[str] = ["Зафиксировать цель и ограничения."]
    if any(word in lowered for word in ("иде", "запиши", "сохрани", "замет")):
        steps.append("Сохранить важную мысль или заметку в память.")
    if any(word in lowered for word in ("задач", "trello", "трелло", "канбан")):
        steps.append("Создать или обновить задачу в Trello через Task Command Center.")
    if any(word in lowered for word in ("календар", "созвон", "встреч", "слот")):
        steps.append("Создать календарный блок, если указан срок или время.")
    if any(word in lowered for word in ("напом", "напомин")):
        steps.append("Поставить напоминание с понятным текстом.")
    if any(word in lowered for word in ("проверь", "проверить", "аудит", "найди")):
        steps.append("Проверить доступные источники и вернуть краткий результат.")
    steps.append("Показать итог и следующие действия.")
    return _dedupe_steps(steps)[:8]


def _extract_explicit_steps(text: str) -> list[str]:
    import re

    matches = re.findall(r"(?:^|[\n;])\s*(?:шаг|пункт|задача)?\s*\d+[\).:\-]\s*([^\n;]+)", text, re.IGNORECASE)
    return _dedupe_steps([" ".join(match.split()) for match in matches if match.strip()])


def _dedupe_steps(steps: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for step in steps:
        normalized = step.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result
