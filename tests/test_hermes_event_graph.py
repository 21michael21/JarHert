from __future__ import annotations

import asyncio

from hermes.native_tools.action_plans import ActionPlanStore, execute_plan
from hermes.native_tools.events import EventStore
from hermes.native_tools.tool_dispatch import invoke_catalog_handler


def make_store(tmp_path) -> EventStore:
    return EventStore(tmp_path / "personal-os.sqlite3")


def test_first_monitor_payload_becomes_silent_baseline(tmp_path) -> None:
    store = make_store(tmp_path)

    result = store.check_monitor(
        name="codex-release",
        source_type="github_releases",
        payload={"tag": "v1", "notes": "first"},
    )

    assert result.status == "baseline"
    assert result.changed is False
    assert result.event_id is None
    assert store.list_events() == []


def test_unchanged_monitor_payload_emits_nothing(tmp_path) -> None:
    store = make_store(tmp_path)
    payload = {"tag": "v1", "notes": "first"}
    store.check_monitor(name="codex-release", source_type="github_releases", payload=payload)

    result = store.check_monitor(name="codex-release", source_type="github_releases", payload=payload)

    assert result.status == "no_change"
    assert result.changed is False
    assert store.list_events() == []


def test_changed_payload_emits_compact_diff_event(tmp_path) -> None:
    store = make_store(tmp_path)
    store.check_monitor(
        name="codex-release",
        source_type="github_releases",
        payload={"tag": "v1", "notes": "first"},
    )

    result = store.check_monitor(
        name="codex-release",
        source_type="github_releases",
        payload={"tag": "v2", "notes": "first", "url": "https://example.test/v2"},
    )

    assert result.status == "changed"
    assert result.changed is True
    assert result.diff == {
        "added": [{"path": "url", "value": "https://example.test/v2"}],
        "removed": [],
        "changed": [{"path": "tag", "before": "v1", "after": "v2"}],
    }
    events = store.list_events()
    assert len(events) == 1
    assert events[0].event_type == "monitor.changed"
    assert events[0].payload["monitor"] == "codex-release"


def test_event_rule_queues_one_allowlisted_action(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_rule(
        name="review-important-release",
        event_type="monitor.changed",
        action_type="evaluate",
        action_config={"condition": "notify only for an important release"},
    )
    store.check_monitor(name="release", source_type="github_releases", payload={"tag": "v1"})
    store.check_monitor(name="release", source_type="github_releases", payload={"tag": "v2"})

    result = store.dispatch_pending_events()

    assert result == {"events": 1, "actions": 1}
    actions = store.list_actions()
    assert len(actions) == 1
    assert actions[0].action_type == "evaluate"
    assert actions[0].payload["config"]["condition"] == "notify only for an important release"


def test_dispatch_is_idempotent(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_rule(name="notify", event_type="monitor.changed", action_type="notify", action_config={})
    store.check_monitor(name="release", source_type="github_releases", payload={"tag": "v1"})
    store.check_monitor(name="release", source_type="github_releases", payload={"tag": "v2"})

    first = store.dispatch_pending_events()
    replay = store.dispatch_pending_events()

    assert first == {"events": 1, "actions": 1}
    assert replay == {"events": 0, "actions": 0}
    assert len(store.list_actions()) == 1


def test_unknown_action_type_is_rejected(tmp_path) -> None:
    store = make_store(tmp_path)

    try:
        store.add_rule(name="bad", event_type="monitor.changed", action_type="shell", action_config={})
    except ValueError as error:
        assert "allowlist" in str(error)
    else:
        raise AssertionError("unsafe event action was accepted")


def test_structured_record_is_idempotent_and_sanitizes_payload(tmp_path) -> None:
    store = make_store(tmp_path)

    first = store.record(
        "tool.invoke_started",
        "tool_dispatch",
        {"token": "secret-value", "prompt": "x" * 700},
        fingerprint="tool:1",
    )
    replay = store.record(
        "tool.invoke_started",
        "tool_dispatch",
        {"token": "changed", "prompt": "new"},
        fingerprint="tool:1",
    )

    assert replay == first
    events = store.list_events()
    assert len(events) == 1
    assert events[0].status == "recorded"
    assert events[0].payload["token"] == "<redacted>"
    assert len(events[0].payload["prompt"]) <= 500


def test_tool_dispatch_records_started_and_succeeded_events(tmp_path) -> None:
    store = make_store(tmp_path)

    def echo(value: str) -> dict[str, str]:
        return {"value": value}

    result = asyncio.run(
        invoke_catalog_handler({"echo": echo}, name="echo", payload={"value": "ok"}, ctx=object(), event_store=store)
    )

    assert result == {"value": "ok"}
    events = store.list_events()
    assert [event.event_type for event in events] == ["tool.invoke_started", "tool.invoke_succeeded"]
    assert events[0].payload["tool"] == "echo"
    assert events[0].payload["payload_keys"] == ["value"]
    assert "payload_hash" in events[0].payload


def test_action_plan_lifecycle_records_structured_events(tmp_path) -> None:
    events = make_store(tmp_path)
    plans = ActionPlanStore(tmp_path / "personal-os.sqlite3", event_store=events)

    class Adapter:
        def create_task(self, **_payload):
            return "created task\ntrello_card_id=abc123"

    plan = plans.create(
        [{"type": "task.create", "payload": {"title": "Проверить лог"}}],
        idempotency_key="plan:events",
    )
    approved = plans.approve(plan.id)
    execute_plan(plans, approved.id, Adapter())

    event_types = [event.event_type for event in events.list_events()]
    assert event_types == [
        "action_plan.create",
        "action_plan.approve",
        "action_plan.action_started",
        "action_plan.action_succeeded",
        "action_plan.finished",
    ]
