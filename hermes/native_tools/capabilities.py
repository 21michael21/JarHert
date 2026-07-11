from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkMode:
    name: str
    reasoning_effort: str
    timeout_seconds: int


@dataclass(frozen=True)
class CapabilityDecision:
    capability: str
    mode: str
    risk: str
    decision: str
    timeout_seconds: int
    reason: str


@dataclass(frozen=True)
class CapabilityRule:
    risk: str
    modes: frozenset[str]


MODES = {
    "fast": WorkMode("fast", "low", 45),
    "think": WorkMode("think", "high", 180),
    "code": WorkMode("code", "high", 900),
}
ALL_MODES = frozenset(MODES)
THINK_CODE = frozenset({"think", "code"})
CODE_ONLY = frozenset({"code"})

CAPABILITIES = {
    "integration.health": CapabilityRule("low", ALL_MODES),
    "system.status": CapabilityRule("low", ALL_MODES),
    "task.list": CapabilityRule("low", ALL_MODES),
    "task.create": CapabilityRule("medium", ALL_MODES),
    "task.move": CapabilityRule("medium", ALL_MODES),
    "task.done": CapabilityRule("medium", ALL_MODES),
    "task.delete": CapabilityRule("high", ALL_MODES),
    "calendar.list": CapabilityRule("low", ALL_MODES),
    "calendar.create": CapabilityRule("medium", ALL_MODES),
    "calendar.move": CapabilityRule("medium", ALL_MODES),
    "calendar.delete": CapabilityRule("high", ALL_MODES),
    "contact.list": CapabilityRule("low", ALL_MODES),
    "contact.write": CapabilityRule("low", ALL_MODES),
    "message.schedule": CapabilityRule("medium", ALL_MODES),
    "message.cancel": CapabilityRule("medium", ALL_MODES),
    "monitor.list": CapabilityRule("low", ALL_MODES),
    "monitor.write": CapabilityRule("medium", ALL_MODES),
    "knowledge.read": CapabilityRule("low", ALL_MODES),
    "knowledge.write": CapabilityRule("medium", ALL_MODES),
    "shopping.read": CapabilityRule("low", ALL_MODES),
    "shopping.write": CapabilityRule("low", ALL_MODES),
    "trip.read": CapabilityRule("low", ALL_MODES),
    "trip.write": CapabilityRule("low", ALL_MODES),
    "trip.cancel": CapabilityRule("medium", ALL_MODES),
    "memory.read": CapabilityRule("low", ALL_MODES),
    "memory.write": CapabilityRule("low", ALL_MODES),
    "note.delete": CapabilityRule("medium", ALL_MODES),
    "note.save": CapabilityRule("low", ALL_MODES),
    "commitment.create": CapabilityRule("low", ALL_MODES),
    "commitment.list": CapabilityRule("low", ALL_MODES),
    "commitment.complete": CapabilityRule("medium", ALL_MODES),
    "reminder.create": CapabilityRule("low", ALL_MODES),
    "reminder.list": CapabilityRule("low", ALL_MODES),
    "reminder.write": CapabilityRule("low", ALL_MODES),
    "crm.read": CapabilityRule("low", ALL_MODES),
    "crm.write": CapabilityRule("low", ALL_MODES),
    "personal.read": CapabilityRule("low", ALL_MODES),
    "skill.feedback": CapabilityRule("low", ALL_MODES),
    "skill.list": CapabilityRule("low", ALL_MODES),
    "subscription.read": CapabilityRule("low", ALL_MODES),
    "subscription.write": CapabilityRule("low", ALL_MODES),
    "project.read": CapabilityRule("low", ALL_MODES),
    "project.write": CapabilityRule("medium", ALL_MODES),
    "telegram.export": CapabilityRule("high", ALL_MODES),
    "personal.export": CapabilityRule("high", ALL_MODES),
    "planner.control": CapabilityRule("medium", ALL_MODES),
    "research.run": CapabilityRule("high", THINK_CODE),
    "sandbox.run": CapabilityRule("high", CODE_ONLY),
}


class CapabilityPolicyStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_mode(self) -> WorkMode:
        with self._connect() as connection:
            row = connection.execute("SELECT mode FROM active_work_mode WHERE id = 1").fetchone()
        return MODES[str(row["mode"])]

    def set_mode(self, mode: str) -> WorkMode:
        clean = str(mode or "").strip().casefold()
        if clean not in MODES:
            raise ValueError("Режим должен быть fast, think или code.")
        with self._connect() as connection:
            connection.execute(
                "UPDATE active_work_mode SET mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                (clean,),
            )
        return MODES[clean]

    def decide(self, capability: str) -> CapabilityDecision:
        clean = str(capability or "").strip().casefold()
        mode = self.get_mode()
        rule = CAPABILITIES.get(clean)
        if rule is None:
            return CapabilityDecision(clean, mode.name, "unknown", "deny", mode.timeout_seconds, "capability_not_allowlisted")
        if mode.name not in rule.modes:
            return CapabilityDecision(clean, mode.name, rule.risk, "deny", mode.timeout_seconds, "capability_blocked_in_mode")
        decision = {"low": "auto", "medium": "confirm", "high": "preview"}[rule.risk]
        return CapabilityDecision(clean, mode.name, rule.risk, decision, mode.timeout_seconds, "allowed")

    def require(self, capability: str) -> CapabilityDecision:
        decision = self.decide(capability)
        if decision.decision == "deny":
            raise PermissionError(f"Capability {capability} запрещена в режиме {decision.mode}.")
        return decision

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS active_work_mode (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO active_work_mode(id, mode) VALUES (1, 'fast');
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
