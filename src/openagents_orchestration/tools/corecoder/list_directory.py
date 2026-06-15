"""Directory listing tool.

Claude Code-style ``ls``: lists files and directories at a path, with an
optional recursion depth and the ability to skip common build/cache
directories. Results are sorted and annotated with file type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

_SKIP_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}
)
_MAX_RESULTS = 200


def _resolve_path(path_str: str, context: RunContext[Any] | None) -> Path:
    """Resolve a path relative to the agent's working directory."""
    path = Path(path_str)
    if path.is_absolute():
        return path
    base: Path | None = None
    if context is not None:
        cached = context.scratch.get("bash_cwd")
        if isinstance(cached, str):
            base = Path(cached)
        else:
            runner = getattr(getattr(context, "deps", None), "runner", None)
            cwd = getattr(runner, "_current_work_dir", None)
            if cwd is not None:
                base = Path(cwd)
    if base is None:
        base = Path.cwd()
    parts = path.parts
    if parts and parts[0] == base.name:
        path = Path(*parts[1:])
    return base / path


class ListDirectoryTool(ToolPlugin):
    """List files and directories at a path."""

    name = "list_directory"
    description = (
        "List files and directories at a path. Returns a tree-like listing "
        "with entries sorted alphabetically. Use this to understand project "
        "structure before reading files."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="readonly",
            reads_files=True,
            default_timeout_ms=10_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list. Defaults to cwd.",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many levels to recurse (0 = only the given path).",
                    "minimum": 0,
                    "maximum": 3,
                    "default": 1,
                },
            },
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        path_str = str(params.get("path", "")).strip() or "."
        depth = max(0, min(3, int(params.get("depth", 1) or 1)))

        path = _resolve_path(path_str, context)
        if not path.exists():
            raise ToolError(f"Path not found: {path_str}", tool_name=self.name)
        if not path.is_dir():
            raise ToolError(f"Not a directory: {path_str}", tool_name=self.name)

        entries = _list_entries(path, depth)
        lines = _render_entries(entries)
        rendered = "\n".join(lines)

        return {
            "path": str(path),
            "depth": depth,
            "entries": entries,
            "message": rendered or "(empty directory)",
        }


def _list_entries(root: Path, max_depth: int) -> list[dict[str, Any]]:
    """Build a flat list of entries with indentation depth."""
    results: list[dict[str, Any]] = []
    _walk(root, root, 0, max_depth, results)
    return results


def _walk(
    root: Path,
    current: Path,
    depth: int,
    max_depth: int,
    results: list[dict[str, Any]],
) -> None:
    if depth > max_depth:
        return
    try:
        items = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except (OSError, PermissionError):
        return
    for item in items:
        if item.name in _SKIP_DIRS:
            continue
        rel = item.relative_to(root)
        kind = "dir" if item.is_dir() else "file"
        results.append(
            {
                "name": item.name,
                "path": str(rel),
                "kind": kind,
                "depth": depth,
            }
        )
        if kind == "dir" and depth < max_depth:
            _walk(root, item, depth + 1, max_depth, results)


def _render_entries(entries: list[dict[str, Any]]) -> list[str]:
    """Render entries as an indented tree."""
    lines: list[str] = []
    for e in entries:
        indent = "  " * e["depth"]
        marker = "📁 " if e["kind"] == "dir" else "  "
        lines.append(f"{indent}{marker}{e['name']}")
    return lines
