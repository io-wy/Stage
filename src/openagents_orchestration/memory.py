"""Persistent CoreCoder session memory.

Stores per-session breadcrumbs that are valuable across runs of the same
session: which files were edited, the last working directory the bash tool
landed in, recent tool-call counts, and short run summaries (one per run).

What gets injected into the prompt is intentionally tiny — overflowing the
system prompt with noisy memory cripples prefix caching. We surface:

- *one line* on the most recent run summary
- *up to 8 file paths* the agent has edited in this session

Anything beyond that lives only in :mod:`memory_view` for the pattern to
look up if it wants to.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from openagents.interfaces.memory import MemoryPlugin


_DEFAULT_DIR = ".agent_memory"
_MAX_SUMMARIES = 10
_MAX_DIRTY_INJECTED = 8


class CoreCoderMemory(MemoryPlugin):
    """JSON-on-disk memory for the CoreCoder agent."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        storage = self.config.get("storage_dir", _DEFAULT_DIR)
        self._storage_dir = Path(storage).expanduser()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._max_summaries = int(self.config.get("max_summaries", _MAX_SUMMARIES))

    def _safe_session_id(self, session_id: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)

    def _path(self, session_id: str) -> Path:
        return self._storage_dir / f"{self._safe_session_id(session_id)}.json"

    def _load(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    async def inject(self, context: Any) -> None:
        record = self._load(context.session_id)
        context.memory_view["corecoder_memory"] = record

        dirty_files = record.get("dirty_files") or []
        if isinstance(dirty_files, list) and dirty_files:
            shown = dirty_files[:_MAX_DIRTY_INJECTED]
            tail = (
                f", ... (+{len(dirty_files) - _MAX_DIRTY_INJECTED} more)"
                if len(dirty_files) > _MAX_DIRTY_INJECTED
                else ""
            )
            context.system_prompt_fragments.append(
                f"# Prior session edits\n- files modified before this run: {', '.join(shown)}{tail}"
            )
            ctx_dirty = context.scratch.setdefault("dirty_files", set())
            if isinstance(ctx_dirty, set):
                ctx_dirty.update(dirty_files)

        cwd = record.get("last_cwd")
        if isinstance(cwd, str) and cwd and not context.scratch.get("bash_cwd"):
            context.scratch["bash_cwd"] = cwd

        summaries = record.get("summaries") or []
        if isinstance(summaries, list) and summaries:
            last_summary = summaries[-1]
            if isinstance(last_summary, dict):
                text = str(last_summary.get("summary", "")).strip()
                if text:
                    context.system_prompt_fragments.append(
                        f"# Last run summary\n{text[:600]}"
                    )

    async def writeback(self, context: Any) -> None:
        record = self._load(context.session_id)

        # Merge dirty files (set on disk, set in memory).
        existing_dirty = record.get("dirty_files") or []
        if not isinstance(existing_dirty, list):
            existing_dirty = []
        in_run = context.scratch.get("dirty_files")
        if isinstance(in_run, set):
            merged = sorted(set(existing_dirty) | in_run)
        else:
            merged = list(existing_dirty)
        record["dirty_files"] = merged

        cwd = context.scratch.get("bash_cwd")
        if isinstance(cwd, str) and cwd:
            record["last_cwd"] = cwd

        # Tool stats (cumulative across runs).
        stats = record.get("tool_stats") or {}
        if not isinstance(stats, dict):
            stats = {}
        counter: Counter[str] = Counter(stats)
        for entry in context.tool_results or []:
            tool_id = entry.get("tool_id")
            if isinstance(tool_id, str):
                counter[tool_id] += 1
        record["tool_stats"] = dict(counter)

        # Append a one-line run summary (the pattern's final text, capped).
        summary_payload = context.state.get("corecoder_summary") if context.state else None
        if not isinstance(summary_payload, str) or not summary_payload.strip():
            summary_payload = ""
        if summary_payload:
            summaries = record.get("summaries") or []
            if not isinstance(summaries, list):
                summaries = []
            summaries.append(
                {
                    "run_id": context.run_id,
                    "summary": summary_payload[:1000],
                }
            )
            record["summaries"] = summaries[-self._max_summaries :]

        self._save(context.session_id, record)

    async def retrieve(self, query: str, context: Any) -> list[dict[str, Any]]:
        record = self._load(context.session_id)
        results: list[dict[str, Any]] = []
        for item in record.get("summaries") or []:
            if isinstance(item, dict) and query.lower() in str(item.get("summary", "")).lower():
                results.append(item)
        return results
