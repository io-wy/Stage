"""Whole-file write tool that tracks dirty paths in ``ctx.scratch``.

Faithful port of CoreCoder's write tool. Always overwrites; creates parent
directories if needed. Records the absolute path in
``context.scratch["dirty_files"]`` (a set) so the pattern can later show a diff
or run a verification pass over only the touched files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class WriteFileTool(ToolPlugin):
    """Overwrite a file with new content; record the path in scratch."""

    name = "write_file"
    description = (
        "Write content to a file, overwriting any existing content. "
        "Creates parent directories. Returns line count."
    )
    durable_idempotent = False  # writes are not safe to replay blindly

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_filesystem",
            writes_files=True,
            default_timeout_ms=10_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        }

    async def invoke(
        self, params: dict[str, Any], context: "RunContext[Any] | None"
    ) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()
        if not file_path:
            raise ToolError("file_path is required", tool_name=self.name)
        content = params.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string", tool_name=self.name)

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)

        if context is not None:
            dirty = context.scratch.setdefault("dirty_files", set())
            if isinstance(dirty, set):
                dirty.add(str(path.resolve(strict=False)))

        return {
            "file_path": str(path),
            "lines_written": n_lines,
            "bytes_written": len(content.encode("utf-8")),
            "message": f"Wrote {n_lines} lines to {file_path}",
        }
