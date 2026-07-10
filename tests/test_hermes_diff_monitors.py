from __future__ import annotations

import json

import pytest

from hermes.native_tools.events import EventStore
from hermes.native_tools.monitors import GitHubReleasesSource, MonitorRegistry, MonitorRunner


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
