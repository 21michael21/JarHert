"""Web search through the DuckDuckGo HTML endpoint with strict limits.

No API key and no browser automation: one GET with a browser-grade User-Agent,
bounded output, and an injectable fetcher so tests never touch the network.
"""

from __future__ import annotations

import html as html_module
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_HTML_BYTES = 400_000


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


Fetcher = Callable[[str], str]


def search_web(
    query: str,
    *,
    limit: int = 6,
    fetcher: Fetcher | None = None,
) -> list[SearchResult]:
    """Return up to ``limit`` organic results for a plain-text query."""
    clean = " ".join(str(query or "").split())
    if not clean or len(clean) > 300:
        raise ValueError("Поисковый запрос должен содержать от 1 до 300 символов.")
    bounded_limit = max(1, min(int(limit), 10))
    fetch = fetcher or _default_fetcher
    page = fetch(f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(clean)}")
    return _parse_results(page)[:bounded_limit]


def _default_fetcher(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https endpoint.
        return response.read(MAX_HTML_BYTES).decode("utf-8", errors="replace")


_RESULT_LINK = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_RESULT_SNIPPET = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")


def _parse_results(page: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    snippets = [_clean_text(match.group("snippet")) for match in _RESULT_SNIPPET.finditer(page)]
    for index, match in enumerate(_RESULT_LINK.finditer(page)):
        url = _unwrap_duckduckgo_redirect(match.group("href"))
        if not url.startswith(("https://", "http://")):
            continue
        results.append(
            SearchResult(
                title=_clean_text(match.group("title")),
                url=url,
                snippet=snippets[index] if index < len(snippets) else "",
            )
        )
    return results


def _unwrap_duckduckgo_redirect(href: str) -> str:
    parsed = urllib.parse.urlparse(html_module.unescape(href))
    target = urllib.parse.parse_qs(parsed.query).get("uddg", [])
    return target[0] if target else html_module.unescape(href)


def _clean_text(fragment: str) -> str:
    return " ".join(html_module.unescape(_TAG.sub(" ", fragment)).split())[:400]
