"""Small, read-only GitHub public API fallback for exact repository URLs."""

from __future__ import annotations

import base64
import copy
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse


FetchJson = Callable[[str, dict[str, str], float], object]
_GITHUB_API = "https://api.github.com"
_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_MAX_RESPONSE_BYTES = 512_000
_MAX_README_CHARS = 4_000
_CACHE_TTL_SECONDS = 300.0


class GitHubPublicReader:
    """Inspect a public repository without a token or GitHub write capability."""

    def __init__(
        self,
        *,
        fetcher: FetchJson | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.clock = clock
        self._cache: dict[str, tuple[float, dict[str, object]]] = {}

    def inspect_repository(self, url: str) -> dict[str, object]:
        owner, repository = parse_github_repository_url(url)
        canonical_url = f"https://github.com/{owner}/{repository}"
        cached = self._cache.get(canonical_url)
        if cached is not None and self.clock() - cached[0] < _CACHE_TTL_SECONDS:
            return copy.deepcopy(cached[1])

        base_url = f"{_GITHUB_API}/repos/{owner}/{repository}"
        metadata = _mapping(self.fetcher(base_url, _headers(), 10), label="репозиторий")
        root = self.fetcher(f"{base_url}/contents/", _headers(), 10)
        readme = self.fetcher(f"{base_url}/readme", _headers(), 10)
        result = {
            "source_url": canonical_url,
            "repository": _text(metadata.get("full_name")) or f"{owner}/{repository}",
            "description": _text(metadata.get("description"), limit=600) or None,
            "language": _text(metadata.get("language"), limit=100) or None,
            "default_branch": _text(metadata.get("default_branch"), limit=100) or None,
            "updated_at": _text(metadata.get("updated_at"), limit=100) or None,
            "stars": _nonnegative_int(metadata.get("stargazers_count")),
            "open_issues": _nonnegative_int(metadata.get("open_issues_count")),
            "archived": bool(metadata.get("archived")),
            "fork": bool(metadata.get("fork")),
            "root_items": _root_items(root),
            "readme_excerpt": _readme_excerpt(readme),
        }
        self._cache[canonical_url] = (self.clock(), result)
        return copy.deepcopy(result)


def parse_github_repository_url(url: str) -> tuple[str, str]:
    parsed = urlparse(str(url or "").strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Нужна точная публичная ссылка вида https://github.com/owner/repo.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("Нужна точная публичная ссылка вида https://github.com/owner/repo.")
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not _REPOSITORY_PART.fullmatch(owner) or not _REPOSITORY_PART.fullmatch(repository):
        raise ValueError("GitHub owner или repository содержит недопустимые символы.")
    return owner, repository


def _fetch_json(url: str, headers: dict[str, str], timeout: float) -> object:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= int(response.status) < 300:
                raise ValueError(f"GitHub вернул HTTP {response.status}.")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404 and url.endswith("/readme"):
            return None
        raise ValueError(f"GitHub вернул HTTP {error.code}.") from error
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Ответ GitHub превышает лимит 512 KB.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("GitHub вернул некорректный JSON.") from error


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JarHert-GitHub-Public/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"GitHub не вернул данные {label}.")
    return value


def _text(value: object, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _root_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), limit=160)
        if not name or "/" in name or "\\" in name:
            continue
        items.append(f"{name}/" if item.get("type") == "dir" else name)
    return items


def _readme_excerpt(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        return ""
    try:
        decoded = base64.b64decode(value["content"], validate=False).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""
    return decoded.strip()[:_MAX_README_CHARS]
