from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

from app.schemas.state import Source

try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
    except Exception:
        DDGS = None

logger = logging.getLogger(__name__)


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 5) -> list[Source]:
        ...


@dataclass
class DuckDuckGoSearchProvider:
    def search(self, query: str, max_results: int = 5) -> list[Source]:
        if DDGS is None:
            return []

        results: list[Source] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                title = item.get("title") or "Untitled"
                href = item.get("href") or item.get("url") or ""
                body = item.get("body") or ""
                if href:
                    results.append(Source(title=title, url=href, snippet=body))
        return results


@dataclass
class StubSearchProvider:
    catalog: dict[str, list[Source]] | None = None

    def search(self, query: str, max_results: int = 5) -> list[Source]:
        if self.catalog:
            lowered = query.lower()
            for key, results in self.catalog.items():
                if key.lower() in lowered:
                    return results[:max_results]

        count = max(1, max_results)
        normalized_query = "-".join(query.strip().lower().split())[:48] or "query"
        return [
            Source(
                title=f"Fallback source {index}",
                url=f"https://example.com/{normalized_query}/{index}",
                snippet=(
                    "No live search provider configured. Placeholder evidence used for "
                    "local workflow validation."
                ),
            )
            for index in range(1, count + 1)
        ]


class WebSearchTool:
    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider or DuckDuckGoSearchProvider()
        self.fallback = StubSearchProvider()

    def search_web(self, query: str, max_results: int = 5) -> list[Source]:
        try:
            results = self.provider.search(query=query, max_results=max_results)
            if results:
                return results
        except Exception:
            logger.exception("Search provider failed; using fallback.")

        return self.fallback.search(query=query, max_results=max_results)
