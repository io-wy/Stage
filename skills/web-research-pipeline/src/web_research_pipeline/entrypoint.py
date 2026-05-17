"""web-research-pipeline entrypoint.

Fetches a list of URLs, extracts readable text, deduplicates paragraphs, and
emits a markdown brief. Uses only the Python standard library (urllib + html).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_SCRIPT_STYLE_TAGS = {"script", "style", "noscript"}
_BLOCK_TAGS = {
    "p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "tr", "td", "th", "pre", "blockquote", "section", "article",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SCRIPT_STYLE_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SCRIPT_STYLE_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._chunks.append(data)

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        lines = [ln.strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _fetch(url: str, timeout: float) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {url}")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "web-research-pipeline/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        ctype = response.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "text" not in ctype.lower():
            raise ValueError(f"Unsupported content type: {ctype}")
        raw_bytes = response.read()
    # Best-effort decoding
    encoding = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype, re.IGNORECASE)
    if m:
        encoding = m.group(1)
    try:
        return raw_bytes.decode(encoding, errors="replace")
    except LookupError:
        return raw_bytes.decode("utf-8", errors="replace")


def _extract_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        # Malformed HTML — fall back to a regex strip
        return re.sub(r"<[^>]+>", " ", html)
    return extractor.get_text()


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.splitlines() if len(p.strip()) > 40]


def _render_report(
    topic: str,
    succeeded: list[tuple[str, str]],
    failed: list[dict[str, str]],
) -> str:
    lines = [
        f"# Research brief: {topic}",
        "",
        f"## Sources",
        "",
    ]
    for url, _ in succeeded:
        lines.append(f"- {url}")
    if failed:
        lines.append("")
        lines.append("### Failed to fetch")
        for f in failed:
            lines.append(f"- {f['url']} — {f['error']}")

    if not succeeded:
        lines.extend(["", "No content was fetched successfully."])
        return "\n".join(lines)

    seen_paragraphs: set[str] = set()
    lines.extend(["", "## Excerpts", ""])
    for url, text in succeeded:
        lines.append(f"### {url}")
        lines.append("")
        emitted = 0
        for para in _paragraphs(text):
            key = para[:200]
            if key in seen_paragraphs:
                continue
            seen_paragraphs.add(key)
            lines.append(para)
            lines.append("")
            emitted += 1
            if emitted >= 5:
                break
        if emitted == 0:
            lines.append("(no unique content beyond what was already shown)")
            lines.append("")

    return "\n".join(lines)


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the web research pipeline."""
    topic = payload.get("topic")
    sources = payload.get("sources")
    output_path = payload.get("output_path")
    max_chars = int(payload.get("max_chars_per_source", 5000))
    timeout = float(payload.get("timeout_seconds", 15.0))

    if not topic:
        raise ValueError("web-research-pipeline: payload must include 'topic'")
    if not sources or not isinstance(sources, list):
        raise ValueError("web-research-pipeline: payload must include 'sources' as a non-empty list")
    if not output_path:
        raise ValueError("web-research-pipeline: payload must include 'output_path'")

    succeeded: list[tuple[str, str]] = []
    failed: list[dict[str, str]] = []
    total_chars = 0

    for url in sources:
        if not isinstance(url, str):
            failed.append({"url": str(url), "error": "non-string URL"})
            continue
        try:
            html = _fetch(url, timeout)
            text = _extract_text(html)
            truncated = text[:max_chars]
            succeeded.append((url, truncated))
            total_chars += len(truncated)
        except urllib.error.HTTPError as exc:
            failed.append({"url": url, "error": f"HTTP {exc.code}"})
        except urllib.error.URLError as exc:
            failed.append({"url": url, "error": f"URL error: {exc.reason}"})
        except (ValueError, TimeoutError) as exc:
            failed.append({"url": url, "error": str(exc)})
        except Exception as exc:
            failed.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    report = _render_report(topic, succeeded, failed)
    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text(report, encoding="utf-8")

    return {
        "report_path": str(output_p),
        "topic": topic,
        "sources_attempted": len(sources),
        "sources_succeeded": len(succeeded),
        "sources_failed": failed,
        "total_chars_extracted": total_chars,
    }
