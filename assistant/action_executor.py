from __future__ import annotations

from dataclasses import dataclass

from assistant.action_schema import PlannedAction
from assistant.tool_registry import ToolContext, ToolExecutionResult, ToolRegistry


@dataclass(frozen=True)
class ActionExecutor:
    registry: ToolRegistry

    def execute(self, action: PlannedAction, context: ToolContext) -> ToolExecutionResult:
        return self.registry.execute(action, context)
