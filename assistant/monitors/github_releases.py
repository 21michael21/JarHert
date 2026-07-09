from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def fetch_latest_github_release(owner: str, repo: str, *, timeout_seconds: float = 10) -> dict[str, Any]:
    clean_owner = _clean_github_segment(owner, "owner")
    clean_repo = _clean_github_segment(repo, "repo")
    url = f"https://api.github.com/repos/{clean_owner}/{clean_repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "jarhert-monitor/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub releases request failed: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError("GitHub releases request failed") from error
    if not isinstance(data, dict):
        raise RuntimeError("GitHub releases returned unsupported payload")
    return {
        "id": data.get("id"),
        "tag_name": data.get("tag_name"),
        "name": data.get("name"),
        "body": data.get("body") or "",
        "html_url": data.get("html_url"),
        "published_at": data.get("published_at"),
        "prerelease": bool(data.get("prerelease")),
        "draft": bool(data.get("draft")),
    }


def _clean_github_segment(value: str, label: str) -> str:
    clean = (value or "").strip()
    if not clean or "/" in clean or ".." in clean:
        raise ValueError(f"Invalid GitHub {label}")
    return clean
