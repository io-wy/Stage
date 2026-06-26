"""Fetch a URL and return its content as markdown.

Claude Code-style web fetch: downloads a page and converts the HTML to readable
markdown. Uses only Python standard library. Falls back to raw text for
non-HTML responses.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class _HTMLToMarkdown(HTMLParser):
    """Minimal HTML-to-markdown converter for web fetch results."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0
        self._in_script = False
        self._link_hrefs: list[str] = []  # stack of hrefs for <a> tags
        self._link_text_parts: list[str] = []  # accumulated text for current link
        self._block_tags = frozenset(
            {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre"}
        )
        self._inline_tags = frozenset({"a", "strong", "b", "em", "i", "code", "span"})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: v for k, v in attrs}
        if tag in ("script", "style", "nav", "footer", "aside"):
            self._skip += 1
            self._in_script = tag in ("script", "style")
            return
        if self._skip > 0:
            return
        if tag in self._block_tags:
            self._parts.append("\n")
        if tag == "h1":
            self._parts.append("# ")
        elif tag == "h2":
            self._parts.append("## ")
        elif tag == "h3":
            self._parts.append("### ")
        elif tag == "h4":
            self._parts.append("#### ")
        elif tag == "li":
            self._parts.append("- ")
        elif tag == "a":
            href = attr_dict.get("href", "")
            if href:
                self._link_hrefs.append(href)
                self._link_text_parts = []
            else:
                self._link_hrefs.append("")
                self._link_text_parts = []
        elif tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag == "code":
            self._parts.append("`")
        elif tag == "pre":
            self._parts.append("\n```\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "aside"):
            self._skip -= 1
            self._in_script = False
            return
        if self._skip > 0:
            return
        if tag == "a":
            href = self._link_hrefs.pop() if self._link_hrefs else ""
            link_text = "".join(self._link_text_parts).strip()
            self._link_text_parts = []
            if href and link_text:
                self._parts.append(f"[{link_text}]({href}) ")
            elif link_text:
                self._parts.append(link_text)
            return
        if tag in self._block_tags:
            self._parts.append("\n")
        elif tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag == "code":
            self._parts.append("`")
        elif tag == "pre":
            self._parts.append("\n```\n")

    def handle_data(self, data: str) -> None:
        if self._skip > 0 or self._in_script:
            return
        text = data.replace("\n", " ").replace("\r", " ")
        if self._link_hrefs:
            self._link_text_parts.append(text)
        else:
            self._parts.append(text)

    def get_markdown(self) -> str:
        raw = "".join(self._parts)
        # Collapse whitespace
        cleaned = re.sub(r"[ \t]+", " ", raw)
        cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
        return cleaned.strip()


class WebFetchTool(ToolPlugin):
    """Fetch a URL and return its content as markdown/plain text."""

    name = "web_fetch"
    description = (
        "Fetch the content of a URL and return it as readable markdown. "
        "Use this to read documentation, issues, or reference pages that "
        "web_search surfaced. No API key required."
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
                "url": {
                    "type": "string",
                    "description": "URL to fetch.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 4000, max 8000).",
                    "minimum": 100,
                    "maximum": 8_000,
                    "default": 4_000,
                },
            },
            "required": ["url"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        url = str(params.get("url", "")).strip()
        if not url:
            raise ToolError("url is required", tool_name=self.name)
        max_chars = max(100, min(8_000, int(params.get("max_chars", 4_000) or 4_000)))

        if not urllib.parse.urlparse(url).scheme:
            url = "https://" + url

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                raw = response.read()
                # Respect charset if present.
                charset = "utf-8"
                if "charset=" in content_type:
                    match = re.search(r"charset=([\w-]+)", content_type)
                    if match:
                        charset = match.group(1)
                text = raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            raise ToolError(
                f"Fetch failed: HTTP {exc.code}", tool_name=self.name
            ) from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"Fetch failed: {exc.reason}", tool_name=self.name) from exc
        except TimeoutError:
            raise ToolError("Fetch timed out", tool_name=self.name) from None

        if "text/html" in content_type:
            converter = _HTMLToMarkdown()
            try:
                converter.feed(text)
                markdown = converter.get_markdown()
            except Exception:
                markdown = text
        else:
            markdown = text

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n... (truncated)"

        return {
            "url": url,
            "content": markdown,
            "message": markdown,
        }
