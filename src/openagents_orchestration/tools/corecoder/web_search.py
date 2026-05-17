"""Web search tool — DuckDuckGo HTML interface (no API key, stdlib only)."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class _ResultExtractor(HTMLParser):
    """Parse DuckDuckGo HTML result page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._results: list[dict[str, str]] = []
        self._in_result = False
        self._in_title = False
        self._in_snippet = False
        self._current: dict[str, str] = {}
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: v for k, v in attrs}
        self._tag_stack.append(tag)

        # DuckDuckGo result links have class="result__a"
        if tag == "a" and attr_dict.get("class") == "result__a":
            self._in_result = True
            self._current = {"title": "", "url": attr_dict.get("href", ""), "snippet": ""}

        if self._in_result and tag in ("a", "h2"):
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if self._in_result and tag in ("a", "h2"):
            self._in_title = False

        # End of a result block
        if tag == "div" and self._in_result and self._current.get("title"):
            self._results.append(dict(self._current))
            self._in_result = False
            self._current = {}

    def handle_data(self, data: str) -> None:
        if self._in_result:
            if self._in_title:
                self._current["title"] = self._current.get("title", "") + data
            elif not self._in_title and self._tag_stack and self._tag_stack[-1] in ("div", "span", "p"):
                self._current["snippet"] = self._current.get("snippet", "") + data

    def get_results(self) -> list[dict[str, str]]:
        # Clean up
        cleaned: list[dict[str, str]] = []
        for r in self._results:
            title = re.sub(r"\s+", " ", r.get("title", "")).strip()
            snippet = re.sub(r"\s+", " ", r.get("snippet", "")).strip()
            url = r.get("url", "").strip()
            if url.startswith("//"):
                url = "https:" + url
            if title and url:
                cleaned.append({"title": title, "url": url, "snippet": snippet})
        return cleaned


class WebSearchTool(ToolPlugin):
    """Search the web via DuckDuckGo HTML interface.

    No API key required. Uses only Python standard library.
    Returns a list of results with title, URL, and snippet.
    """

    name = "web_search"
    description = (
        "Search the web for a query. Returns a list of results with "
        "title, URL, and snippet. Use this to find documentation, "
        "tutorials, or reference material. No API key required."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="none",
            default_timeout_ms=30_000,
            interrupt_behavior="cancel",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5, max 10).",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        query = str(params.get("query", "")).strip()
        if not query:
            raise ToolError("query is required", tool_name=self.name)
        max_results = min(int(params.get("max_results", 5)), 10)

        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                html = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise ToolError(f"Search failed: HTTP {exc.code}", tool_name=self.name) from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"Search failed: {exc.reason}", tool_name=self.name) from exc
        except TimeoutError:
            raise ToolError("Search timed out", tool_name=self.name)

        extractor = _ResultExtractor()
        try:
            extractor.feed(html)
        except Exception:
            # Malformed HTML — try regex fallback
            pass

        results = extractor.get_results()[:max_results]

        if not results:
            return {
                "query": query,
                "results": [],
                "message": f"No results found for '{query}'. Try a different query.",
            }

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   URL: {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet'][:200]}")
            lines.append("")

        return {
            "query": query,
            "results": results,
            "message": "\n".join(lines),
        }
