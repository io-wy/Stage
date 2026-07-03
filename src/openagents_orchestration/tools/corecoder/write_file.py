"""Whole-file write tool that tracks dirty paths in ``ctx.scratch``.

Faithful port of CoreCoder's write tool. Always overwrites; creates parent
directories if needed. Records the absolute path in
``context.scratch["dirty_files"]`` (a set) so the pattern can later show a diff
or run a verification pass over only the touched files.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.corecoder._paths import (
    resolve_agent_path as _resolve_path,
)


class WriteFileTool(ToolPlugin):
    """Overwrite a file with new content; record the path in scratch."""

    name = "write_file"
    description = (
        "Write a complete file, overwriting any existing content entirely. "
        "Use it to create new files or fully rewrite small ones.\n\n"
        "# Effects\n"
        "- Replaces the whole file with `content` (no merge); missing parent "
        "directories are created automatically.\n"
        "- Records the path as dirty for the run's verification pass.\n"
        "- Returns lines_written, bytes_written, and the resolved absolute file_path.\n\n"
        "# When to use\n"
        "- Creating a brand-new file.\n"
        "- Rewriting a short file where a full replacement is clearer than a patch.\n\n"
        "# When NOT to use\n"
        "- Changing a few lines of an existing file — use edit_file (cheaper, and it "
        "won't clobber unrelated code).\n"
        "- A change spanning several files or hunks — use apply_patch.\n"
        "- You only have the changed lines, not the whole file — write_file needs the "
        "COMPLETE content, or it truncates the file down to just what you pass.\n\n"
        "# Paths\n"
        "- file_path may be absolute, or relative to your working directory (the cwd "
        "shown in your task input); relative is preferred.\n"
        "- Do NOT prepend the work directory's own name: if cwd is `.eval_work`, use "
        "`src/foo.py`, not `.eval_work/src/foo.py` (that leading segment is auto-stripped).\n\n"
        "# Common mistakes\n"
        "- Passing a partial snippet as `content` — everything else in the file is lost.\n"
        "- Using write_file for a one-line tweak instead of edit_file."
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
                "file_path": {
                    "type": "string",
                    "description": "File to write. Absolute, or relative to your working directory.",
                },
                "content": {
                    "type": "string",
                    "description": "Complete new file content — replaces the entire file.",
                },
            },
            "required": ["file_path", "content"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()
        if not file_path:
            raise ToolError("file_path is required", tool_name=self.name)
        content = params.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string", tool_name=self.name)

        path = _resolve_path(file_path, context)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        n_lines = content.count("\n") + (
            0 if content.endswith("\n") or not content else 1
        )

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
