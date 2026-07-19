"""Minimal GitHub write operations through the official REST API.

Token comes from the environment (fine-grained PAT). Each call is one POST;
the module never stores or logs the token.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Callable


Fetcher = Callable[[str, dict[str, Any]], dict[str, Any]]

_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def create_repository(
    name: str,
    *,
    description: str = "",
    private: bool = True,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    """Create one repository in the token owner's account; return its key URLs."""
    clean_name = str(name or "").strip()
    if not _REPO_NAME.fullmatch(clean_name) or clean_name.startswith((".", "-")):
        raise ValueError("Имя репозитория: 1-100 символов A-Za-z0-9._- и не с точки/дефиса в начале.")
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_PERSONAL_ACCESS_TOKEN не настроен.")
    fetch = fetcher or _default_fetcher
    payload = fetch(
        "https://api.github.com/user/repos",
        {
            "name": clean_name,
            "description": " ".join(str(description or "").split())[:350],
            "private": bool(private),
        },
        token=token,
    )
    return {
        "name": str(payload.get("name") or clean_name),
        "full_name": str(payload.get("full_name") or ""),
        "html_url": str(payload.get("html_url") or ""),
        "clone_url": str(payload.get("clone_url") or ""),
        "private": bool(payload.get("private", private)),
    }


def _default_fetcher(url: str, body: dict[str, Any], *, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "jarhert-native-tools",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https endpoint.
        return json.loads(response.read(200_000).decode("utf-8"))
