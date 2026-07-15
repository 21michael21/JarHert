from __future__ import annotations

from pathlib import Path

import pytest

from hermes.native_tools.github_public import GitHubPublicReader, parse_github_repository_url
from hermes.native_tools.mcp_api import NativeToolsAPI


def test_public_github_reader_returns_a_bounded_repo_snapshot_and_caches_it() -> None:
    calls: list[str] = []
    payloads = {
        "https://api.github.com/repos/acme/reader": {
            "full_name": "acme/reader",
            "html_url": "https://github.com/acme/reader",
            "description": "A clean reader.",
            "language": "Python",
            "default_branch": "main",
            "updated_at": "2026-07-15T10:00:00Z",
            "stargazers_count": 12,
            "open_issues_count": 3,
            "archived": False,
            "fork": False,
        },
        "https://api.github.com/repos/acme/reader/contents/": [
            {"name": "README.md", "type": "file"},
            {"name": "src", "type": "dir"},
        ],
        "https://api.github.com/repos/acme/reader/readme": {
            "content": "IyBSZWFkZXIKClNoYXJwIHRleHQgcmVhZGVyLg==",
            "encoding": "base64",
        },
    }

    def fetch(url: str, _headers: dict[str, str], _timeout: float):
        calls.append(url)
        return payloads[url]

    reader = GitHubPublicReader(fetcher=fetch)
    first = reader.inspect_repository("https://github.com/acme/reader")
    second = reader.inspect_repository("https://github.com/acme/reader/")

    assert first == second
    assert calls == list(payloads)
    assert first["repository"] == "acme/reader"
    assert first["root_items"] == ["README.md", "src/"]
    assert first["readme_excerpt"] == "# Reader\n\nSharp text reader."


def test_public_github_reader_rejects_non_repository_urls_and_api_is_low_risk(tmp_path) -> None:
    for url in (
        "https://github.com/acme",
        "https://github.com/acme/reader/issues",
        "https://example.test/acme/reader",
        "https://github.com/acme/reader?token=secret",
    ):
        with pytest.raises(ValueError):
            parse_github_repository_url(url)

    api = NativeToolsAPI(
        database_path=tmp_path / "personal-os.sqlite3",
        github_public_fetcher=lambda url, *_args: {
            "https://api.github.com/repos/acme/reader": {
                "full_name": "acme/reader",
                "html_url": "https://github.com/acme/reader",
                "description": None,
                "language": None,
                "default_branch": "main",
                "updated_at": "2026-07-15T10:00:00Z",
                "stargazers_count": 0,
                "open_issues_count": 0,
                "archived": False,
                "fork": False,
            },
            "https://api.github.com/repos/acme/reader/contents/": [],
            "https://api.github.com/repos/acme/reader/readme": None,
        }[url],
    )

    assert api.github_public_repository(url="https://github.com/acme/reader")["repository"] == "acme/reader"


def test_profile_exposes_the_public_fallback_without_expanding_official_mcp_permissions() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "github-research" / "SKILL.md").read_text(encoding="utf-8")

    assert "- github_public_repository" in config
    assert "--read-only" in config
    assert "--lockdown-mode" in config
    assert "mcp_jarhert_native_github_public_repository" in skill
    assert "cannot inspect private repositories" in skill
