from __future__ import annotations

import pytest

from hermes.native_tools.github_write import create_repository
from hermes.native_tools.mcp_api import NativeToolsAPI


def test_create_repository_posts_validated_payload(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "test-token")
    calls: list[tuple[str, dict]] = []

    def fetch(url: str, body: dict, *, token: str) -> dict:
        calls.append((url, body))
        assert token == "test-token"
        return {
            "name": body["name"],
            "full_name": f"owner/{body['name']}",
            "html_url": f"https://github.com/owner/{body['name']}",
            "clone_url": f"https://github.com/owner/{body['name']}.git",
            "private": body["private"],
        }

    repo = create_repository("jarvis-lab", description="тест", fetcher=fetch)

    assert calls[0][1]["name"] == "jarvis-lab"
    assert calls[0][1]["private"] is True
    assert repo["full_name"] == "owner/jarvis-lab"
    assert repo["clone_url"].endswith(".git")


def test_create_repository_rejects_bad_names_and_missing_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "test-token")
    with pytest.raises(ValueError, match="Имя репозитория"):
        create_repository(".evil", fetcher=lambda *args, **kwargs: {})
    with pytest.raises(ValueError, match="Имя репозитория"):
        create_repository("with space", fetcher=lambda *args, **kwargs: {})

    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    with pytest.raises(RuntimeError, match="GITHUB_PERSONAL_ACCESS_TOKEN"):
        create_repository("ok-name", fetcher=lambda *args, **kwargs: {})


def test_native_api_repo_create_requires_confirmation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        "hermes.native_tools.api_integrations.create_repository",
        lambda name, *, description, private: {"name": name, "full_name": f"owner/{name}"},
    )
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    with pytest.raises(ValueError, match="подтверждение"):
        api.github_repo_create(name="jarvis-lab")

    created = api.github_repo_create(name="jarvis-lab", confirmed=True)

    assert created["full_name"] == "owner/jarvis-lab"
