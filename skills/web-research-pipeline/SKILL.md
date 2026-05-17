---
name: web-research-pipeline
description: Aggregate web content from a list of URLs into a structured research brief. Fetches each URL, strips HTML to text, deduplicates paragraphs, and assembles a markdown report. The caller provides sources — this skill does not search.
---

# Web Research Pipeline

Use this skill when you have a topic and a curated list of source URLs and want a quick reading brief without manually opening each tab.

The orchestrator (or user) is responsible for finding sources via search tools (e.g. `tavily_search`). This skill takes those URLs and produces a synthesized brief.

## Inputs

- `topic` (required, str) — the research subject, used as the report title.
- `sources` (required, list[str]) — HTTP/HTTPS URLs to fetch.
- `output_path` (required, str) — where to write the markdown brief.
- `max_chars_per_source` (optional, int) — per-source body cap. Default 5000.
- `timeout_seconds` (optional, float) — per-URL fetch timeout. Default 15.

## Outputs

```json
{
  "report_path": "/path/to/brief.md",
  "topic": "rust async runtimes",
  "sources_attempted": 5,
  "sources_succeeded": 4,
  "sources_failed": [
    {"url": "https://...", "error": "timeout"}
  ],
  "total_chars_extracted": 18432
}
```

## Behavior

- Fetches each URL via `urllib` (no external dependency).
- Strips HTML tags and collapses whitespace to produce readable text.
- Deduplicates identical paragraphs across sources.
- Produces a markdown report: title, source list, key excerpts per source.
- Failures (HTTP error, timeout, non-HTML) are collected and reported, not raised.
