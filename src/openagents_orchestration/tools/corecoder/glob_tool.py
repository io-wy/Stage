"""Glob tool that returns paths sorted by mtime (newest first).

Faithful port of CoreCoder's glob tool. Caps results at ``MAX_RESULTS`` so a
sloppy pattern like ``**/*`` cannot wedge the LLM context. Filters out
non-files (directories) and unreadable entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


_MAX_RESULTS = 100


class GlobTool(ToolPlugin):
    """Pattern-match file paths, mtime-sorted (most recently modified first)."""

    name = "glob"
    description = (
        "Find files by glob pattern (e.g. '**/*.py', 'src/**/test_*.py'). "
        "Results are sorted by modification time (newest first), capped at 100."
    )

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="readonly",
            reads_files=True,
            default_timeout_ms=15_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to glob from. Defaults to the CWD.",
                },
            },
            "required": ["pattern"],
        }

    async def invoke(
        self, params: dict[str, Any], context: "RunContext[Any] | None"
    ) -> dict[str, Any]:
        pattern = str(params.get("pattern", "")).strip()
        if not pattern:
            raise ToolError("pattern is required", tool_name=self.name)
        base = Path(str(params.get("path", "")).strip() or ".").resolve(strict=False)
        if not base.exists():
            raise ToolError(f"Base path not found: {base}", tool_name=self.name)
        if not base.is_dir():
            raise ToolError(f"Base path is not a directory: {base}", tool_name=self.name)

        try:
            hits = [p for p in base.glob(pattern) if p.is_file()]
        except (OSError, ValueError) as exc:
            raise ToolError(f"Glob failed: {exc}", tool_name=self.name) from exc

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        hits.sort(key=_mtime, reverse=True)
        total = len(hits)
        capped = hits[:_MAX_RESULTS]

        return {
            "pattern": pattern,
            "base": str(base),
            "matches": [str(p) for p in capped],
            "total_matches": total,
            "capped": total > _MAX_RESULTS,
            "message": (
                f"... ({total} matches, showing first {_MAX_RESULTS})"
                if total > _MAX_RESULTS
                else f"{total} match(es)"
            ),
        }
