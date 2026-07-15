from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .database import open_personal_os_database
from .tool_catalog import CAPABILITY_SPECS


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
    capability: CapabilityRule(spec.risk, spec.modes)
    for capability, spec in CAPABILITY_SPECS.items()
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
        return open_personal_os_database(self.database_path)
