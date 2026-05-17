"""Search-and-replace file editor with strict uniqueness.

Faithful port of CoreCoder's edit tool: ``old_string`` must appear exactly
once in the file, otherwise the call returns an error so the LLM has to
include more surrounding context. After a successful edit the path is added
to ``context.scratch["dirty_files"]`` and a unified diff is returned so
both the user and the LLM can see what changed.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ModelRetryError, ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


_MAX_DIFF_CHARS = 3000


class EditFileTool(ToolPlugin):
    """Replace an exact unique substring inside a file."""

    name = "edit_file"
    description = (
        "Edit a file by replacing an exact substring match. The old_string MUST "
        "appear exactly once; include enough surrounding context to make it unique. "
        "Returns a unified diff."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="writes_filesystem",
            reads_files=True,
            writes_files=True,
            default_timeout_ms=10_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File to edit."},
                "old_string": {
                    "type": "string",
                    "description": "Exact substring to find. Must appear exactly once.",
                },
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def invoke(
        self, params: dict[str, Any], context: "RunContext[Any] | None"
    ) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()
        old_string = params.get("old_string")
        new_string = params.get("new_string")
        if not file_path:
            raise ToolError("file_path is required", tool_name=self.name)
        if not isinstance(old_string, str) or not old_string:
            raise ToolError("old_string must be a non-empty string", tool_name=self.name)
        if not isinstance(new_string, str):
            raise ToolError("new_string must be a string", tool_name=self.name)

        path = Path(file_path).expanduser()
        if not path.exists():
            raise ToolError(f"File not found: {file_path}", tool_name=self.name)
        if not path.is_file():
            raise ToolError(f"Not a regular file: {file_path}", tool_name=self.name)

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                f"File is not UTF-8 text: {file_path} ({exc.reason})",
                tool_name=self.name,
            ) from exc

        occurrences = content.count(old_string)
        if occurrences == 0:
            preview = content[:500] + ("..." if len(content) > 500 else "")
            # ModelRetryError tells the SDK to feed the message back to the LLM
            # so it can adjust the next call instead of giving up the run.
            raise ModelRetryError(
                f"old_string not found in {file_path}. "
                f"Verify the exact text. File starts with:\n{preview}"
            )
        if occurrences > 1:
            raise ModelRetryError(
                f"old_string appears {occurrences} times in {file_path}. "
                "Include more surrounding lines to make it unique."
            )

        new_content = content.replace(old_string, new_string, 1)
        path.write_text(new_content, encoding="utf-8")

        if context is not None:
            dirty = context.scratch.setdefault("dirty_files", set())
            if isinstance(dirty, set):
                dirty.add(str(path.resolve(strict=False)))

        diff = _unified_diff(content, new_content, str(path))
        return {
            "file_path": str(path),
            "diff": diff,
            "occurrences_replaced": 1,
            "message": f"Edited {file_path}\n{diff}",
        }


def _unified_diff(old: str, new: str, filename: str, *, context: int = 3) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context,
    )
    rendered = "".join(diff)
    if len(rendered) > _MAX_DIFF_CHARS:
        rendered = rendered[: _MAX_DIFF_CHARS - 200] + "\n... (diff truncated)\n"
    return rendered
