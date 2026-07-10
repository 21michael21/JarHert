from __future__ import annotations

from hermes.native_tools.events import EventStore


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
