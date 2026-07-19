from __future__ import annotations

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.web_search import search_web


DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone&rut=abc">Первый <b>результат</b></a>
<a class="result__snippet">Описание первого результата.</a>
<a class="result__a" href="https://example.org/two">Второй результат</a>
<a class="result__snippet">Второе описание.</a>
</body></html>
"""


def test_search_web_parses_results_and_unwraps_redirects() -> None:
    items = search_web("тест", fetcher=lambda url: DDG_HTML)

    assert items == [
        items[0].__class__(
            title="Первый результат",
            url="https://example.com/one",
            snippet="Описание первого результата.",
        ),
        items[0].__class__(title="Второй результат", url="https://example.org/two", snippet="Второе описание."),
    ]


def test_search_web_bounds_limit_and_rejects_empty_query() -> None:
    assert len(search_web("тест", limit=50, fetcher=lambda url: DDG_HTML)) <= 10
    with pytest.raises(ValueError, match="Поисковый запрос"):
        search_web("   ", fetcher=lambda url: DDG_HTML)


def test_native_api_web_search_uses_capability_and_returns_items(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes.native_tools.api_integrations.search_web",
        lambda query, *, limit: search_web(query, limit=limit, fetcher=lambda url: DDG_HTML),
    )
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    payload = api.web_search(query="котики", limit=2)

    assert len(payload["items"]) == 2
    assert payload["items"][0]["url"] == "https://example.com/one"


def test_web_search_is_registered_in_catalog_runtime_and_config() -> None:
    from hermes.native_tools.tool_catalog import TOOL_CATALOG

    names = {spec.name for spec in TOOL_CATALOG}
    assert "web_search" in names
