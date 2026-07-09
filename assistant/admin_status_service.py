from __future__ import annotations

from assistant.limits import DailyLimitStore
from assistant.types import UserContext


def build_admin_status_text(
    *,
    user: UserContext,
    limits: DailyLimitStore,
    provider_health,
    delivery_outbox,
    task_center,
) -> str:
    remaining = limits.remaining_for_user(user.user_id)
    lines = [
        "Admin status",
        f"user_id={user.user_id}",
        f"tg_user_id={user.tg_user_id}",
        f"remaining_today={remaining}",
    ]
    lines.extend(provider_health_lines(provider_health))
    lines.extend(delivery_health_lines(delivery_outbox))
    lines.extend(task_center_health_lines(task_center))
    return "\n".join(lines)


def provider_health_lines(provider_health) -> list[str]:
    if provider_health is None:
        return []
    items = provider_health.list_all()
    if not items:
        return []
    lines = ["Providers:"]
    for item in items:
        status = "cooldown" if item.in_cooldown() else "ok"
        latency = f" {item.latency_ms}ms" if item.latency_ms is not None else ""
        counters = (
            f" rate={item.rate_limit_count}"
            f" server={item.server_error_count}"
            f" auth={item.auth_error_count}"
        )
        if status == "ok":
            lines.append(f"{item.name} {item.model} ok{latency}")
        else:
            lines.append(f"{item.name} {item.model} cooldown{counters}")
    return lines


def delivery_health_lines(delivery_outbox) -> list[str]:
    if delivery_outbox is None:
        return []
    stats = delivery_outbox.stats()
    return [
        "Delivery:",
        (
            f"queued={stats.get('queued', 0)} "
            f"sending={stats.get('sending', 0)} "
            f"sent={stats.get('sent', 0)} "
            f"failed={stats.get('failed', 0)}"
        ),
    ]


def task_center_health_lines(task_center) -> list[str]:
    if task_center is None or not hasattr(task_center, "health_check"):
        return []
    try:
        health = task_center.health_check()
    except Exception as exc:
        return ["Task Center:", f"health=fail detail={type(exc).__name__}: {exc}"]
    trello = "ok" if health.trello_ok else "fail"
    calendar = "ok" if health.calendar_ok else "fail"
    return [
        "Task Center:",
        f"trello={trello} calendar={calendar}",
    ]


def build_perf_status_text(events, *, limit: int = 200) -> str:
    if events is None or not hasattr(events, "recent_perf_samples"):
        return "Perf status\nperf_events=disabled"
    samples = events.recent_perf_samples(limit=limit)
    lines = [
        "Perf status",
        f"samples={len(samples)}",
    ]
    if not samples:
        lines.append("Нет свежих perf_ms событий.")
        return "\n".join(lines)

    keys = [
        "total_response_ms",
        "intent_parse_ms",
        "route_ms",
        "llm_ms",
        "tool_ms",
    ]
    for key in keys:
        values = [sample[key] for sample in samples if key in sample]
        if not values:
            continue
        lines.append(
            f"{key}: count={len(values)} p50={_percentile(values, 50)}ms p95={_percentile(values, 95)}ms"
        )
    return "\n".join(lines)


def _percentile(values: list[int], percentile: int) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = round((len(ordered) - 1) * (percentile / 100))
    return ordered[max(0, min(index, len(ordered) - 1))]
