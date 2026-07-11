from __future__ import annotations

import json

import pytest

from hermes.native_tools.events import EventStore
from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.monitors import (
    AllowedJsonSource,
    AllowedUrlSource,
    GitHubReleasesSource,
    MonitorRegistry,
    MonitorRunner,
    RssSource,
)


def github_payload(tag: str) -> bytes:
    return json.dumps(
        {
            "tag_name": tag,
            "name": f"Release {tag}",
            "html_url": f"https://github.test/releases/{tag}",
            "published_at": "2026-07-10T10:00:00Z",
            "body": "A concise changelog",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "artifact.zip"}],
        }
    ).encode()


def test_github_source_fetches_small_normalized_payload() -> None:
    requested: list[str] = []

    def fetch(url: str, headers: dict[str, str], timeout: float) -> bytes:
        requested.append(url)
        assert headers["Accept"] == "application/vnd.github+json"
        assert timeout == 10
        return github_payload("v2")

    source = GitHubReleasesSource(fetch_bytes=fetch)

    result = source.fetch({"owner": "openai", "repo": "codex"})

    assert requested == ["https://api.github.com/repos/openai/codex/releases/latest"]
    assert result == {
        "tag": "v2",
        "name": "Release v2",
        "url": "https://github.test/releases/v2",
        "published_at": "2026-07-10T10:00:00Z",
        "notes": "A concise changelog",
        "draft": False,
        "prerelease": False,
        "assets": ["artifact.zip"],
    }


def test_monitor_runner_is_silent_until_payload_changes(tmp_path) -> None:
    registry = MonitorRegistry(tmp_path / "personal-os.sqlite3")
    registry.add(
        name="codex-release",
        source_type="github_releases",
        source_config={"owner": "openai", "repo": "codex"},
        condition="Напиши только если релиз важный.",
    )
    payloads = iter([github_payload("v1"), github_payload("v1"), github_payload("v2")])
    source = GitHubReleasesSource(fetch_bytes=lambda *_args: next(payloads))
    runner = MonitorRunner(registry, EventStore(registry.database_path), sources={"github_releases": source})

    baseline = runner.run_once()
    no_change = runner.run_once()
    changed = runner.run_once()

    assert baseline == []
    assert no_change == []
    assert len(changed) == 1
    assert changed[0]["monitor"] == "codex-release"
    assert changed[0]["condition"] == "Напиши только если релиз важный."
    assert changed[0]["diff"]["changed"][0]["path"] == "name"
    assert changed[0]["current"]["tag"] == "v2"


def test_disabled_monitor_is_not_fetched(tmp_path) -> None:
    registry = MonitorRegistry(tmp_path / "personal-os.sqlite3")
    monitor = registry.add(
        name="codex-release",
        source_type="github_releases",
        source_config={"owner": "openai", "repo": "codex"},
        condition="important only",
    )
    registry.disable(monitor.id)
    source = GitHubReleasesSource(fetch_bytes=lambda *_args: pytest.fail("disabled monitor fetched"))
    runner = MonitorRunner(registry, EventStore(registry.database_path), sources={"github_releases": source})

    assert runner.run_once() == []


def test_monitor_registry_rejects_unknown_source(tmp_path) -> None:
    registry = MonitorRegistry(tmp_path / "personal-os.sqlite3")

    with pytest.raises(ValueError, match="allowlist"):
        registry.add(name="unsafe", source_type="shell", source_config={}, condition="run")


def test_rss_json_and_text_sources_are_small_and_allowlisted() -> None:
    payloads = {
        "https://example.test/feed.xml": b"<rss><channel><title>News</title><item><title>Release</title><link>https://example.test/r</link></item></channel></rss>",
        "https://api.example.test/state": b'{"status":"ready","count":2}',
        "https://example.test/page": b"<html><script>steal()</script><body><h1>Important</h1><p>Changed text</p></body></html>",
    }

    def fetch(url: str, _headers: dict[str, str], _timeout: float) -> bytes:
        return payloads[url]

    rss = RssSource(fetch_bytes=fetch).fetch(
        {"url": "https://example.test/feed.xml", "allowed_hosts": ["example.test"]}
    )
    api = AllowedJsonSource(fetch_bytes=fetch).fetch(
        {"url": "https://api.example.test/state", "allowed_hosts": ["api.example.test"]}
    )
    page = AllowedUrlSource(fetch_bytes=fetch).fetch(
        {"url": "https://example.test/page", "allowed_hosts": ["example.test"]}
    )

    assert rss["items"][0]["title"] == "Release"
    assert api["payload"] == {"status": "ready", "count": 2}
    assert page["text"] == "Important Changed text"
    assert "steal" not in page["text"]


def test_monitor_source_rejects_host_outside_explicit_allowlist() -> None:
    source = AllowedJsonSource(fetch_bytes=lambda *_args: b"{}")

    with pytest.raises(ValueError, match="allowlist"):
        source.fetch({"url": "https://private.example/state", "allowed_hosts": ["public.example"]})


def test_quiet_hours_defer_changed_monitor_to_one_digest(tmp_path) -> None:
    registry = MonitorRegistry(tmp_path / "personal-os.sqlite3")
    registry.add(
        name="feed",
        source_type="rss",
        source_config={
            "url": "https://example.test/feed.xml",
            "allowed_hosts": ["example.test"],
            "quiet_hours": "00:00-23:59",
            "timezone": "UTC",
        },
        condition="Сообщи об изменении",
    )
    payloads = iter([{"items": [{"title": "v1"}]}, {"items": [{"title": "v2"}]}])

    class Source:
        def fetch(self, _config):
            return next(payloads)

    runner = MonitorRunner(registry, EventStore(registry.database_path), sources={"rss": Source()})

    assert runner.run_once(now="2030-01-05T12:00:00+00:00") == []
    assert runner.run_once(now="2030-01-05T12:00:00+00:00") == []
    digest = registry.build_digest()

    assert len(digest["items"]) == 1
    assert digest["items"][0]["monitor"] == "feed"
    registry.mark_digest_delivered(digest["item_ids"])
    assert registry.build_digest() == {"items": [], "item_ids": []}


def test_daily_emit_budget_defers_extra_changes(tmp_path) -> None:
    registry = MonitorRegistry(tmp_path / "personal-os.sqlite3")
    for name in ("first", "second"):
        registry.add(
            name=name,
            source_type="rss",
            source_config={"url": f"https://example.test/{name}", "allowed_hosts": ["example.test"]},
            condition="Сообщи",
        )
    payloads = iter([{"v": 1}, {"v": 1}, {"v": 2}, {"v": 2}])

    class Source:
        def fetch(self, _config):
            return next(payloads)

    runner = MonitorRunner(registry, EventStore(registry.database_path), sources={"rss": Source()})
    now = "2030-01-05T12:00:00+00:00"
    assert runner.run_once(now=now, daily_emit_limit=1) == []

    changes = runner.run_once(now=now, daily_emit_limit=1)

    assert len(changes) == 1
    assert len(registry.build_digest()["items"]) == 1


def test_native_api_manages_allowlisted_source_and_digest(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    created = api.monitor_add_source(
        name="news",
        source_type="rss",
        url="https://example.test/feed.xml",
        allowed_hosts=["example.test"],
        condition="Только важное",
        quiet_hours="23:00-08:00",
        timezone_name="Europe/Moscow",
    )

    assert created["source_type"] == "rss"
    assert created["source_config"]["quiet_hours"] == "23:00-08:00"
    assert api.monitor_list()["items"] == [created]
