"""Regex content search across a directory tree.

Faithful port of CoreCoder's grep tool. Skips well-known build/cache dirs,
caps results at 200 matches, and refuses to walk more than 5_000 files so a
sloppy ``.*`` pattern cannot drag the agent loop into a multi-minute scan.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.corecoder._paths import resolve_agent_path

_SKIP_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}
)
_MAX_MATCHES = 200
_MAX_FILES_WALKED = 5_000


class GrepTool(ToolPlugin):
    """Regex search returning ``file:line: text`` rows."""

    name = "grep"
    description = (
        "用 Python 正则搜索文件内容；返回 'file:line: text' 行。\n\n"
        "返回：\n"
        "- matches（最多 200 条）、match_count、files_walked、capped 标志\n"
        "- 跳过 .git/node_modules/__pycache__/.venv/dist/build；最多遍历 5000 个文件\n\n"
        "用途：\n"
        "- 查找符号、字符串或模式在项目中出现在哪里\n"
        "- 修改函数前定位所有调用点（变更影响扫描）\n\n"
        "不要用：\n"
        "- 按文件名查找文件——用 glob\n"
        "- 读取已知文件——用 read_file\n\n"
        "参数：\n"
        "- pattern（必填）是 Python 正则；path 是要搜索的文件或目录\n"
        "- path 可以是绝对路径或相对于工作目录（cwd）"
        "shown in your task input); defaults to '.'.\n"
        "- include is an optional filename glob, e.g. '*.py'.\n\n"
        "# Common mistakes\n"
        "- An unanchored '.*' that hits the 200-cap — tighten the regex or scope with path/include."
    )

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="readonly",
            reads_files=True,
            default_timeout_ms=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex."},
                "path": {
                    "type": "string",
                    "description": "File or directory to search. Absolute or relative to your working directory; defaults to '.'.",
                },
                "include": {
                    "type": "string",
                    "description": "Glob filter (e.g. '*.py') applied to file names.",
                },
            },
            "required": ["pattern"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        pattern = str(params.get("pattern", "")).strip()
        if not pattern:
            raise ToolError("pattern is required", tool_name=self.name)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regex: {exc}", tool_name=self.name) from exc

        base_str = str(params.get("path", "")).strip() or "."
        include = params.get("include")
        if include is not None and not isinstance(include, str):
            raise ToolError("include must be a string glob", tool_name=self.name)

        base = resolve_agent_path(str(Path(base_str).expanduser()), context).resolve(
            strict=False
        )
        if not base.exists():
            raise ToolError(f"Path not found: {base_str}", tool_name=self.name)

        files = [base] if base.is_file() else _walk(base, include)

        matches: list[str] = []
        files_walked = 0
        for fp in files:
            files_walked += 1
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{fp}:{lineno}: {line.rstrip()}")
                    if len(matches) >= _MAX_MATCHES:
                        return {
                            "pattern": pattern,
                            "matches": matches,
                            "match_count": len(matches),
                            "files_walked": files_walked,
                            "capped": True,
                            "message": (
                                f"... ({_MAX_MATCHES} match limit reached, "
                                f"showing first {_MAX_MATCHES})"
                            ),
                        }

        return {
            "pattern": pattern,
            "matches": matches,
            "match_count": len(matches),
            "files_walked": files_walked,
            "capped": False,
            "message": (
                f"{len(matches)} match(es)" if matches else "No matches found."
            ),
        }


def _walk(root: Path, include: str | None) -> list[Path]:
    results: list[Path] = []
    for item in root.rglob(include or "*"):
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        if item.is_file():
            results.append(item)
            if len(results) >= _MAX_FILES_WALKED:
                break
    return results
