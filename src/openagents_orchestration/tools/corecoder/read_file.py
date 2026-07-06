"""Read a file with numbered lines and offset/limit windows.

Faithful port of CoreCoder's read tool. The ``offset`` is 1-based to match
``grep -n`` / editor line numbers. Output truncates with a footer telling the
LLM how many lines remain so it can decide whether to re-read with a wider
window.
"""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.corecoder._paths import (
    resolve_agent_path as _resolve_path,
)

_DEFAULT_LIMIT = 2000
_MAX_LIMIT = 5000


class ReadFileTool(ToolPlugin):
    """Numbered-line file reader with paging."""

    name = "read_file"
    description = (
        "读取 UTF-8 文本文件，每行带行号。"
        "编辑前复制精确文本，或验证声称的产物。\n\n"
        "参数：\n"
        "- file_path（必填）：绝对路径或相对于工作目录\n"
        "- offset（默认 1）：起始行号\n"
        "- limit（默认 2000，最大 5000）：返回行数\n\n"
        "规则：\n"
        "- 复制 read_file 输出用于 edit_file 的 old_string 时，去掉行号前缀\n"
        "- 跨文件查找符号用 grep_tool；列出文件用 list_directory"
    )

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
                "file_path": {"type": "string", "description": "Path to the file."},
                "offset": {
                    "type": "integer",
                    "description": "1-based line to start from.",
                    "minimum": 1,
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of lines to return.",
                    "minimum": 1,
                    "maximum": _MAX_LIMIT,
                    "default": _DEFAULT_LIMIT,
                },
            },
            "required": ["file_path"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()
        if not file_path:
            raise ToolError("file_path is required", tool_name=self.name)
        offset = max(1, int(params.get("offset", 1) or 1))
        limit = max(
            1,
            min(_MAX_LIMIT, int(params.get("limit", _DEFAULT_LIMIT) or _DEFAULT_LIMIT)),
        )

        path = _resolve_path(file_path, context)
        if not path.exists():
            raise ToolError(f"File not found: {file_path}", tool_name=self.name)
        if not path.is_file():
            raise ToolError(f"Not a regular file: {file_path}", tool_name=self.name)

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                f"File is not UTF-8 text: {file_path} ({exc.reason})",
                tool_name=self.name,
            ) from exc

        all_lines = text.splitlines()
        total = len(all_lines)
        start = offset - 1
        chunk = all_lines[start : start + limit]
        numbered = "\n".join(f"{start + i + 1}\t{ln}" for i, ln in enumerate(chunk))

        if total > start + len(chunk):
            footer = (
                f"\n\n... ({total} lines total, showing "
                f"{start + 1}-{start + len(chunk)})"
            )
            numbered += footer

        return {
            "file_path": str(path),
            "content": numbered,
            "total_lines": total,
            "shown_lines": len(chunk),
            "offset": offset,
        }
