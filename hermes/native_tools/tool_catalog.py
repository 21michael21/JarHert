"""Single contract for JarHert's native MCP tools and capability policy.

The MCP runtime keeps typed wrappers close to their user-facing confirmation
copy. This module owns the stable metadata those wrappers share: the public
tool name, API handler, required capabilities, risk, and future-facing bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ToolBundle(StrEnum):
    OPERATIONS = "operations"
    PLANNING = "planning"
    PERSONAL = "personal"
    RESEARCH = "research"
    CODE = "code"


@dataclass(frozen=True)
class CapabilitySpec:
    risk: str
    modes: frozenset[str]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: str
    capabilities: tuple[str, ...]
    risk: str
    bundle: ToolBundle
    enabled_by_default: bool = True


_DISCOVERY_HINTS: dict[str, tuple[str, ...]] = {
    "task": ("задач", "trello", "карточ", "колонк", "приоритет", "перенес", "закрой"),
    "calendar": ("календар", "встреч", "событи", "созвон", "расписан"),
    "memory": ("памят", "замет", "иде", "обещан", "предпочтен"),
    "note": ("замет", "иде", "сохрани", "найди", "памят"),
    "contact": ("контакт", "илье", "напис", "сообщен", "crm"),
    "message": ("сообщен", "напис", "отправ", "контакт"),
    "reminder": ("напомин", "напомни", "завтра", "повтор"),
    "monitor": ("монитор", "релиз", "rss", "радар", "изменен"),
    "knowledge": ("сайт", "ссылк", "архив", "знани", "исслед"),
    "github": ("github", "реп", "pr", "issue", "ci", "гитхаб"),
    "web": ("поиск", "гугл", "интернет", "найди", "погугл", "загугл", "веб"),
    "expense": ("трат", "потратил", "расход", "деньг", "бюджет", "подпис"),
    "project": ("проект", "статус", "отчёт", "прогресс", "дедлайн"),
    "coding": ("код", "реп", "баг", "тест", "diff", "codex"),
    "voice": ("голос", "голосов", "диктов", "расшифров", "термин"),
    "action_plan": ("создай", "сделай", "перенес", "план", "несколько"),
}


_ALL_MODES = frozenset({"fast", "think", "code"})
_CODE_ONLY = frozenset({"code"})


CAPABILITY_SPECS = {
    "integration.health": CapabilitySpec("low", _ALL_MODES),
    "system.status": CapabilitySpec("low", _ALL_MODES),
    "task.list": CapabilitySpec("low", _ALL_MODES),
    "task.create": CapabilitySpec("medium", _ALL_MODES),
    "task.move": CapabilitySpec("medium", _ALL_MODES),
    "task.priority": CapabilitySpec("medium", _ALL_MODES),
    "task.done": CapabilitySpec("medium", _ALL_MODES),
    "task.delete": CapabilitySpec("high", _ALL_MODES),
    "calendar.list": CapabilitySpec("low", _ALL_MODES),
    "calendar.create": CapabilitySpec("medium", _ALL_MODES),
    "calendar.move": CapabilitySpec("medium", _ALL_MODES),
    "calendar.delete": CapabilitySpec("high", _ALL_MODES),
    "contact.list": CapabilitySpec("low", _ALL_MODES),
    "contact.write": CapabilitySpec("low", _ALL_MODES),
    "message.schedule": CapabilitySpec("medium", _ALL_MODES),
    "message.cancel": CapabilitySpec("medium", _ALL_MODES),
    "monitor.list": CapabilitySpec("low", _ALL_MODES),
    "monitor.write": CapabilitySpec("medium", _ALL_MODES),
    "knowledge.read": CapabilitySpec("low", _ALL_MODES),
    "knowledge.write": CapabilitySpec("medium", _ALL_MODES),
    "github.read": CapabilitySpec("low", _ALL_MODES),
    "shopping.read": CapabilitySpec("low", _ALL_MODES),
    "shopping.write": CapabilitySpec("low", _ALL_MODES),
    "trip.read": CapabilitySpec("low", _ALL_MODES),
    "trip.write": CapabilitySpec("low", _ALL_MODES),
    "trip.cancel": CapabilitySpec("medium", _ALL_MODES),
    "memory.read": CapabilitySpec("low", _ALL_MODES),
    "memory.write": CapabilitySpec("low", _ALL_MODES),
    "note.delete": CapabilitySpec("medium", _ALL_MODES),
    "note.save": CapabilitySpec("low", _ALL_MODES),
    "commitment.create": CapabilitySpec("low", _ALL_MODES),
    "commitment.list": CapabilitySpec("low", _ALL_MODES),
    "commitment.complete": CapabilitySpec("medium", _ALL_MODES),
    "reminder.create": CapabilitySpec("low", _ALL_MODES),
    "reminder.list": CapabilitySpec("low", _ALL_MODES),
    "reminder.write": CapabilitySpec("low", _ALL_MODES),
    "crm.read": CapabilitySpec("low", _ALL_MODES),
    "crm.write": CapabilitySpec("low", _ALL_MODES),
    "personal.read": CapabilitySpec("low", _ALL_MODES),
    "skill.feedback": CapabilitySpec("low", _ALL_MODES),
    "skill.list": CapabilitySpec("low", _ALL_MODES),
    "coding.read": CapabilitySpec("low", _ALL_MODES),
    "coding.queue": CapabilitySpec("high", _ALL_MODES),
    "subscription.read": CapabilitySpec("low", _ALL_MODES),
    "subscription.write": CapabilitySpec("low", _ALL_MODES),
    "project.read": CapabilitySpec("low", _ALL_MODES),
    "project.write": CapabilitySpec("medium", _ALL_MODES),
    "telegram.export": CapabilitySpec("high", _ALL_MODES),
    "telegram.export.read": CapabilitySpec("low", _ALL_MODES),
    "personal.export": CapabilitySpec("high", _ALL_MODES),
    "planner.control": CapabilitySpec("medium", _ALL_MODES),
    "research.run": CapabilitySpec("high", _ALL_MODES),
    "sandbox.run": CapabilitySpec("high", _CODE_ONLY),
    "web.search": CapabilitySpec("low", _ALL_MODES),
    "github.write": CapabilitySpec("high", _ALL_MODES),
    "finance.read": CapabilitySpec("low", _ALL_MODES),
    "finance.write": CapabilitySpec("low", _ALL_MODES),
}


def _tool(
    name: str,
    handler: str,
    capabilities: tuple[str, ...],
    risk: str,
    bundle: ToolBundle,
    *,
    enabled_by_default: bool = True,
) -> ToolSpec:
    return ToolSpec(name, handler, capabilities, risk, bundle, enabled_by_default)


_PLAN_CAPABILITIES = (
    "task.create", "task.move", "task.priority", "task.done", "task.delete",
    "calendar.create", "calendar.move", "calendar.delete",
    "note.save", "commitment.create", "reminder.create",
)


TOOL_CATALOG = (
    _tool("integration_health", "integration_health", ("integration.health",), "low", ToolBundle.OPERATIONS),
    _tool("system_status", "system_status", ("system.status",), "low", ToolBundle.OPERATIONS),
    _tool("tool_catalog_discover", "tool_catalog_discover", (), "low", ToolBundle.OPERATIONS),
    _tool("tool_catalog_invoke", "tool_catalog_invoke", (), "low", ToolBundle.OPERATIONS),
    _tool("work_mode_get", "work_mode_get", (), "low", ToolBundle.OPERATIONS),
    _tool("work_mode_set", "work_mode_set", ("planner.control",), "medium", ToolBundle.OPERATIONS),
    _tool("voice_inbox_prepare", "voice_inbox_prepare", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("voice_vocabulary_add", "voice_vocabulary_add", ("memory.write",), "low", ToolBundle.PERSONAL),
    _tool("voice_vocabulary_list", "voice_vocabulary_list", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("task_list", "task_list", ("task.list",), "low", ToolBundle.PLANNING),
    _tool("calendar_list", "calendar_list", ("calendar.list",), "low", ToolBundle.PLANNING),
    _tool("action_plan_confirm_execute", "action_plan_confirm_execute", _PLAN_CAPABILITIES, "high", ToolBundle.PLANNING),
    _tool("action_plan_dag_confirm_execute", "action_plan_dag_confirm_execute", _PLAN_CAPABILITIES, "high", ToolBundle.PLANNING),
    _tool("action_plan_status", "action_plan_get", (), "low", ToolBundle.PLANNING),
    _tool("action_plan_trace", "action_plan_trace", (), "low", ToolBundle.PLANNING),
    _tool("action_plan_pause_confirmed", "action_plan_pause", ("planner.control",), "medium", ToolBundle.PLANNING),
    _tool("action_plan_resume_confirmed", "action_plan_resume", ("planner.control",), "medium", ToolBundle.PLANNING),
    _tool("contact_add", "contact_add", ("contact.write",), "low", ToolBundle.PERSONAL),
    _tool("contact_list", "contact_list", ("contact.list",), "low", ToolBundle.PERSONAL),
    _tool("message_plan_confirm_schedule", "message_plan_confirm_schedule", ("message.schedule",), "medium", ToolBundle.PERSONAL),
    _tool("message_plan_cancel_confirmed", "message_plan_cancel", ("message.cancel",), "medium", ToolBundle.PERSONAL),
    _tool("memory_block_upsert", "memory_block_upsert", ("memory.write",), "low", ToolBundle.PERSONAL),
    _tool("memory_block_list", "memory_block_list", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("memory_context", "memory_context", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("note_search", "note_search", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("note_edit", "note_edit", ("memory.write",), "low", ToolBundle.PERSONAL),
    _tool("note_history", "note_history", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("note_delete_confirmed", "note_delete", ("note.delete",), "medium", ToolBundle.PERSONAL),
    _tool("memory_consolidation_list", "memory_consolidation_list", ("memory.read",), "low", ToolBundle.PERSONAL),
    _tool("project_context_upsert", "project_context_upsert", ("project.write",), "medium", ToolBundle.PERSONAL),
    _tool("project_context_list", "project_context_list", ("project.read",), "low", ToolBundle.PERSONAL),
    _tool("project_context_resolve", "project_context_resolve", ("project.read",), "low", ToolBundle.PERSONAL),
    _tool("commitment_list", "commitment_list", ("commitment.list",), "low", ToolBundle.PERSONAL),
    _tool("commitment_complete_confirmed", "commitment_complete", ("commitment.complete",), "medium", ToolBundle.PERSONAL),
    _tool("reminder_create", "reminder_create", ("reminder.create",), "low", ToolBundle.PERSONAL),
    _tool("reminder_list", "reminder_list", ("reminder.list",), "low", ToolBundle.PERSONAL),
    _tool("reminder_reschedule", "reminder_reschedule", ("reminder.write",), "low", ToolBundle.PERSONAL),
    _tool("reminder_cancel", "reminder_cancel", ("reminder.write",), "low", ToolBundle.PERSONAL),
    _tool("crm_interaction_log", "crm_interaction_log", ("crm.write",), "low", ToolBundle.PERSONAL),
    _tool("crm_timeline", "crm_timeline", ("crm.read",), "low", ToolBundle.PERSONAL),
    _tool("personal_today", "personal_today", ("personal.read",), "low", ToolBundle.PERSONAL),
    _tool("personal_daily_brief", "personal_daily_brief", ("personal.read",), "low", ToolBundle.PERSONAL),
    _tool("personal_weekly_review", "personal_weekly_review", ("personal.read",), "low", ToolBundle.PERSONAL),
    _tool("shopping_add", "shopping_add", ("shopping.write",), "low", ToolBundle.PERSONAL),
    _tool("shopping_list", "shopping_list", ("shopping.read",), "low", ToolBundle.PERSONAL),
    _tool("shopping_mark_bought", "shopping_mark_bought", ("shopping.write",), "low", ToolBundle.PERSONAL),
    _tool("shopping_remove", "shopping_remove", ("shopping.write",), "low", ToolBundle.PERSONAL),
    _tool("trip_create", "trip_create", ("trip.write",), "low", ToolBundle.PERSONAL),
    _tool("trip_list", "trip_list", ("trip.read",), "low", ToolBundle.PERSONAL),
    _tool("trip_details", "trip_details", ("trip.read",), "low", ToolBundle.PERSONAL),
    _tool("trip_add_item", "trip_add_item", ("trip.write",), "low", ToolBundle.PERSONAL),
    _tool("trip_item_complete", "trip_item_complete", ("trip.write",), "low", ToolBundle.PERSONAL),
    _tool("trip_cancel_confirmed", "trip_cancel", ("trip.cancel",), "medium", ToolBundle.PERSONAL),
    _tool("subscription_create", "subscription_create", ("subscription.write",), "low", ToolBundle.PERSONAL),
    _tool("expense_add", "expense_add", ("finance.write",), "low", ToolBundle.PERSONAL),
    _tool("expense_list", "expense_list", ("finance.read",), "low", ToolBundle.PERSONAL),
    _tool("expense_monthly", "expense_monthly", ("finance.read",), "low", ToolBundle.PERSONAL),
    _tool("project_status_report", "project_status_report", ("project.read",), "low", ToolBundle.PERSONAL),
    _tool("subscription_list", "subscription_list", ("subscription.read",), "low", ToolBundle.PERSONAL),
    _tool("subscription_update", "subscription_update", ("subscription.write",), "low", ToolBundle.PERSONAL),
    _tool("subscription_cancel", "subscription_cancel", ("subscription.write",), "low", ToolBundle.PERSONAL),
    _tool("skill_feedback", "skill_feedback", ("skill.feedback",), "low", ToolBundle.PERSONAL),
    _tool("skill_candidates", "skill_candidates", ("skill.list",), "low", ToolBundle.PERSONAL),
    _tool("skill_mark_staged", "skill_mark_staged", ("skill.feedback",), "low", ToolBundle.PERSONAL),
    _tool("monitor_add_github_releases", "monitor_add_github_releases", ("monitor.write",), "medium", ToolBundle.RESEARCH),
    _tool("monitor_add_source", "monitor_add_source", ("monitor.write",), "medium", ToolBundle.RESEARCH),
    _tool("monitor_list", "monitor_list", ("monitor.list",), "low", ToolBundle.RESEARCH),
    _tool("monitor_digest", "monitor_digest", ("monitor.list",), "low", ToolBundle.RESEARCH),
    _tool("monitor_digest_mark_delivered", "monitor_digest_mark_delivered", ("monitor.write",), "medium", ToolBundle.RESEARCH),
    _tool("monitor_disable", "monitor_disable", ("monitor.write",), "medium", ToolBundle.RESEARCH),
    _tool("monitor_schedule_update", "monitor_schedule_update", ("monitor.write",), "medium", ToolBundle.RESEARCH, enabled_by_default=False),
    _tool("knowledge_archive_url_confirmed", "knowledge_archive_url", ("knowledge.write",), "medium", ToolBundle.RESEARCH),
    _tool("knowledge_archive_urls_confirmed", "knowledge_archive_urls", ("knowledge.write",), "medium", ToolBundle.RESEARCH),
    _tool("knowledge_search", "knowledge_search", ("knowledge.read",), "low", ToolBundle.RESEARCH),
    _tool("knowledge_source_excerpt", "knowledge_source_excerpt", ("knowledge.read",), "low", ToolBundle.RESEARCH),
    _tool("knowledge_list_sources", "knowledge_list_sources", ("knowledge.read",), "low", ToolBundle.RESEARCH),
    _tool("github_public_repository", "github_public_repository", ("github.read",), "low", ToolBundle.RESEARCH),
    _tool("web_search", "web_search", ("web.search",), "low", ToolBundle.RESEARCH),
    _tool("github_repo_create_confirmed", "github_repo_create_confirmed", ("github.write",), "high", ToolBundle.CODE),
    _tool("telegram_text_export_confirmed", "telegram_text_export_confirmed", ("telegram.export",), "high", ToolBundle.RESEARCH),
    _tool("telegram_file_download_confirmed", "telegram_file_download_confirmed", ("telegram.export",), "high", ToolBundle.RESEARCH),
    _tool("telegram_text_export_excerpt", "telegram_text_export_excerpt", ("telegram.export.read",), "low", ToolBundle.RESEARCH),
    _tool("telegram_file_read_excerpt", "telegram_file_read_excerpt", ("telegram.export.read",), "low", ToolBundle.RESEARCH),
    _tool("telegram_text_export_queue_analysis_confirmed", "telegram_text_export_queue_analysis", ("telegram.export.read", "research.run"), "high", ToolBundle.RESEARCH),
    _tool("coding_job_enqueue_confirmed", "coding_job_enqueue", ("coding.queue", "research.run"), "high", ToolBundle.CODE),
    _tool("coding_job_list", "coding_job_list", ("coding.read",), "low", ToolBundle.CODE),
    _tool("coding_job_get", "coding_job_get", ("coding.read",), "low", ToolBundle.CODE, enabled_by_default=False),
)

_TOOLS_BY_NAME = {spec.name: spec for spec in TOOL_CATALOG}


def tool_spec(name: str) -> ToolSpec:
    try:
        return _TOOLS_BY_NAME[name]
    except KeyError as error:
        raise KeyError(f"Unknown JarHert native tool: {name}") from error


def configured_tool_names(config_path: str | Path) -> set[str]:
    """Read JarHert's native tool include list without adding a YAML dependency."""
    lines = Path(config_path).read_text(encoding="utf-8").splitlines()
    in_native_server = in_tools = in_include = False
    tools: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if line.startswith("  jarhert_native:"):
            in_native_server = True
            continue
        if in_native_server and line.startswith("  ") and not line.startswith("    ") and stripped:
            break
        if in_native_server and line.startswith("    tools:"):
            in_tools = True
            continue
        if in_tools and line.startswith("      include:"):
            in_include = True
            continue
        if in_include:
            if line.startswith("        - "):
                tools.add(stripped.removeprefix("- "))
                continue
            if stripped:
                break
    return tools


def tool_names_for_bundle(bundle: ToolBundle, *, enabled_by_default: bool = False) -> tuple[str, ...]:
    return tuple(
        spec.name
        for spec in TOOL_CATALOG
        if spec.bundle == bundle and (not enabled_by_default or spec.enabled_by_default)
    )


def active_tool_bundles(value: str | None = None) -> set[ToolBundle]:
    """Parse an explicit MCP bundle selection; operations remain observable."""
    raw = (value or "all").strip().casefold()
    # Hermes leaves an optional ${VAR} reference intact when the variable is
    # absent. Treat this one known config placeholder as the documented
    # default, while keeping genuinely invalid bundle values explicit errors.
    if not raw or raw in {"all", "${hermes_tool_bundles}"}:
        return set(ToolBundle)
    selected: set[ToolBundle] = {ToolBundle.OPERATIONS}
    for item in raw.split(","):
        clean = item.strip()
        if not clean:
            continue
        try:
            selected.add(ToolBundle(clean))
        except ValueError as error:
            allowed = ", ".join(bundle.value for bundle in ToolBundle)
            raise ValueError(f"Unknown HERMES_TOOL_BUNDLES value {clean!r}; expected one of: {allowed}, all") from error
    return selected


def tool_names_for_active_bundles(value: str | None = None) -> tuple[str, ...]:
    selected = active_tool_bundles(value)
    return tuple(spec.name for spec in TOOL_CATALOG if spec.bundle in selected)


def tool_is_active(spec: ToolSpec, value: str | None = None) -> bool:
    return spec.bundle in active_tool_bundles(value)


def discover_tool_specs(
    query: str = "",
    *,
    bundle: ToolBundle | None = None,
    limit: int = 8,
) -> tuple[ToolSpec, ...]:
    """Return a small relevant slice of the catalog without changing policy."""
    clean_query = str(query or "").strip().casefold()
    tokens = tuple(token for token in clean_query.replace("_", " ").split() if len(token) > 1)
    candidates = [spec for spec in TOOL_CATALOG if bundle is None or spec.bundle == bundle]

    def score(spec: ToolSpec) -> tuple[int, int, str]:
        haystack = " ".join((spec.name, spec.handler, spec.bundle.value, *spec.capabilities)).casefold()
        hint_tokens = (
            _DISCOVERY_HINTS.get(spec.name.split("_", 1)[0], ())
            + _DISCOVERY_HINTS.get(spec.handler.split("_", 1)[0], ())
            + (_DISCOVERY_HINTS["action_plan"] if spec.name.startswith("action_plan_") else ())
        )
        matches = sum(3 for token in tokens if token in haystack)
        matches += sum(2 for hint in hint_tokens if hint and hint in clean_query)
        if spec.name == "action_plan_confirm_execute" and any(
            marker in clean_query for marker in ("созда", "сдела", "перенес", "измени", "отмени", "закрой")
        ):
            matches += 5
        risk_rank = {"low": 0, "medium": 1, "high": 2}[spec.risk]
        return (-matches, risk_rank, spec.name)

    ranked = sorted(candidates, key=score)
    if tokens:
        matched = [spec for spec in ranked if score(spec)[0] < 0]
        if matched:
            ranked = matched
    return tuple(ranked[: max(1, min(int(limit), 12))])


def tool_input_contract(spec: ToolSpec) -> str:
    if spec.name.startswith("action_plan_"):
        return "JSON plan with only documented action fields; unknown mutation fields are rejected."
    if spec.name.endswith(("_list", "_search", "_status", "_get", "_trace", "_discover")):
        return "Typed optional filters only."
    if "confirmed" in spec.name:
        return "Typed payload plus one explicit Telegram confirmation."
    return "Typed payload only."


def tool_output_contract(spec: ToolSpec) -> str:
    if spec.name.startswith("action_plan_"):
        return "Persisted plan state; trace returns only counts, next step, and compact problems."
    if spec.name.endswith(("_list", "_search", "_history", "_candidates", "_discover")):
        return "JSON object with an items array."
    if spec.name.endswith("_trace"):
        return "Compact JSON status for one plan."
    return "JSON object with the created, updated, or requested result."


def tool_catalog_entry(spec: ToolSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "bundle": spec.bundle.value,
        "risk": spec.risk,
        "capabilities": list(spec.capabilities),
        "input_contract": tool_input_contract(spec),
        "output_contract": tool_output_contract(spec),
    }


def validate_tool_catalog() -> list[str]:
    """Return configuration-independent contract errors for tests and CI."""
    errors: list[str] = []
    names = [spec.name for spec in TOOL_CATALOG]
    if len(names) != len(set(names)):
        errors.append("duplicate_tool_name")
    for spec in TOOL_CATALOG:
        if not spec.name or not spec.handler:
            errors.append(f"invalid_tool:{spec.name or '<unnamed>'}")
        for capability in spec.capabilities:
            if capability not in CAPABILITY_SPECS:
                errors.append(f"unknown_capability:{spec.name}:{capability}")
    return errors
