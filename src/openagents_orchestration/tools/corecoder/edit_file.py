"""Search-and-replace file editor with strict uniqueness.

Faithful port of CoreCoder's edit tool: ``old_string`` must appear exactly
once in the file, otherwise the call returns an error so the LLM has to
include more surrounding context. After a successful edit the path is added
to ``context.scratch["dirty_files"]`` and a unified diff is returned so
both the user and the LLM can see what changed.
"""

from __future__ import annotations

import contextlib
import difflib
from typing import Any

from openagents.errors.exceptions import ModelRetryError, ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.store.artifact_store import infer_task_id
from openagents_orchestration.tools.corecoder._paths import (
    resolve_agent_path as _resolve_path,
)

_MAX_DIFF_CHARS = 3000


class EditFileTool(ToolPlugin):
    """Replace an exact unique substring inside a file."""

    name = "edit_file"
    description = (
        "Edit a file by replacing one exact, unique substring. "
        "The preferred tool for changing an existing file — surgical and cheap.\n\n"
        "# Effects\n"
        "- Replaces the single occurrence of `old_string` with `new_string` and "
        "returns a unified diff.\n"
        "- Records the path as dirty and shares the new content via the ArtifactStore.\n\n"
        "# When to use\n"
        "- Any targeted change to an existing file: fix a line, rename a local symbol, "
        "tweak a value.\n\n"
        "# When NOT to use\n"
        "- Creating a new file or fully rewriting one — use write_file.\n"
        "- A change spanning many files or hunks — use apply_patch.\n"
        "- The change is described in prose, not an exact string you can copy — use "
        "semantic_edit.\n\n"
        "# Matching rules\n"
        "- `old_string` must appear EXACTLY ONCE. Zero matches or multiple matches are "
        "rejected, and you must retry with more surrounding context.\n"
        "- Copy `old_string` verbatim from read_file output (whitespace and indentation "
        "included), but DROP the leading line-number-and-tab prefix.\n\n"
        "# Paths\n"
        "- file_path may be absolute, or relative to your working directory (the cwd "
        "shown in your task input); relative is preferred.\n"
        "- Do NOT prepend the work directory's own name: if cwd is `.eval_work`, use "
        "`src/foo.py`, not `.eval_work/src/foo.py` (that leading segment is auto-stripped).\n\n"
        "# Common mistakes\n"
        "- Too little context, so `old_string` matches several places (rejected).\n"
        "- Pasting the leading line-number-and-tab prefix from read_file into "
        "`old_string` (it won't match)."
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
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()
        old_string = params.get("old_string")
        new_string = params.get("new_string")
        if not file_path:
            raise ToolError("file_path is required", tool_name=self.name)
        if not isinstance(old_string, str) or not old_string:
            raise ToolError(
                "old_string must be a non-empty string", tool_name=self.name
            )
        if not isinstance(new_string, str):
            raise ToolError("new_string must be a string", tool_name=self.name)

        path = _resolve_path(file_path, context)
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

            # Push to ArtifactStore for inter-agent sharing (best-effort)
            store = getattr(getattr(context, "deps", None), "artifact_store", None)
            if store is not None:
                agent_id = getattr(context, "agent_id", "")
                task_id = infer_task_id(agent_id)
                with contextlib.suppress(Exception):
                    await store.put(task_id, str(path), new_content)

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
