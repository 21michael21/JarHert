from __future__ import annotations

from pathlib import Path

import pytest

from hermes.native_tools.knowledge_archive import KnowledgeArchive, validate_archive_url
from hermes.native_tools.mcp_api import NativeToolsAPI


def test_archiving_one_page_deduplicates_snapshots_and_makes_it_searchable(tmp_path) -> None:
    pages = [
        b"<html><head><title>OAuth guide</title><script>drop me</script></head>"
        b"<body><h1>OAuth refresh tokens</h1><p>Keep the refresh token safe.</p></body></html>",
        b"<html><head><title>OAuth guide v2</title></head>"
        b"<body><p>Rotate the refresh token after an incident.</p></body></html>",
    ]

    def fetch(_url: str, _headers: dict[str, str], _timeout: float) -> bytes:
        return pages[0]

    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3", knowledge_fetcher=fetch)
    first = api.knowledge_archive_url(url="https://docs.example.test/oauth", project="Hub_ML")
    replay = api.knowledge_archive_url(url="https://docs.example.test/oauth", project="Hub_ML")

    assert first["changed"] is True
    assert replay["changed"] is False
    assert replay["snapshot_count"] == 1
    assert first["title"] == "OAuth guide"
    assert api.knowledge_search(query="refresh token", project="Hub_ML")["items"][0]["source_url"] == first["url"]

    pages[0] = pages[1]
    changed = api.knowledge_archive_url(url="https://docs.example.test/oauth", project="Hub_ML")

    assert changed["changed"] is True
    assert changed["snapshot_count"] == 2
    assert api.knowledge_search(query="incident", project="Hub_ML")["items"][0]["title"] == "OAuth guide v2"


def test_archive_rejects_unsafe_urls_and_oversized_pages(tmp_path) -> None:
    for value in (
        "http://example.test/page",
        "https://user:pass@example.test/page",
        "https://127.0.0.1/private",
        "https://example.test:8443/page",
    ):
        with pytest.raises(ValueError):
            validate_archive_url(value)

    archive = KnowledgeArchive(tmp_path / "personal-os.sqlite3", fetcher=lambda *_args: b"x" * 1_000_001)
    with pytest.raises(ValueError, match="1 MB"):
        archive.archive_url("https://docs.example.test/page")


def test_archive_keeps_a_bounded_history_of_changed_snapshots(tmp_path) -> None:
    version = {"value": 0}

    def fetch(_url: str, _headers: dict[str, str], _timeout: float) -> bytes:
        version["value"] += 1
        return f"<title>Guide {version['value']}</title><p>Version {version['value']}.</p>".encode()

    archive = KnowledgeArchive(tmp_path / "personal-os.sqlite3", fetcher=fetch)
    result = {}
    for _ in range(22):
        result = archive.archive_url("https://docs.example.test/guide")

    assert result["snapshot_count"] == 20
    assert archive.search("Version 22")[0]["title"] == "Guide 22"


def test_knowledge_archive_is_exposed_as_low_read_and_confirmed_write(tmp_path) -> None:
    api = NativeToolsAPI(
        database_path=tmp_path / "personal-os.sqlite3",
        knowledge_fetcher=lambda *_args: b"<title>Short</title><p>Useful text.</p>",
    )
    captured = api.knowledge_archive_url(url="https://example.test/guide")

    assert captured["title"] == "Short"
    assert api.knowledge_list_sources()["items"] == [
        {
            "id": captured["source_id"],
            "url": "https://example.test/guide",
            "title": "Short",
            "project": None,
            "snapshot_count": 1,
            "updated_at": captured["updated_at"],
        }
    ]


def test_knowledge_tools_are_in_the_profile_and_skill_guides_one_page_only() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "personal-knowledge" / "SKILL.md").read_text(encoding="utf-8")

    assert "- knowledge_archive_url_confirmed" in config
    assert "- knowledge_search" in config
    assert "- knowledge_list_sources" in config
    assert "never crawl" in skill.casefold()
    assert "mcp_jarhert_native_knowledge_archive_url_confirmed" in skill
