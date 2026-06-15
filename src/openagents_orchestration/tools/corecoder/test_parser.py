"""Parse test/lint command output into structured failure records.

CoreCoder's verification loop uses this to turn long pytest/ruff stderr into
short `{file, line, error}` snippets that fit back into the context window.
"""

from __future__ import annotations

import re
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

# pytest failure line: "src/foo.py::test_name - AssertionError: msg"
_PYTEST_FAILURE_RE = re.compile(
    r"^(?P<file>[^\s:]+\.py)::(?P<test>[^\s]+)\s+-\s+(?P<error>.+)$",
    re.MULTILINE,
)

# Compiler-style line: "src/foo.py:42: error: ..." or "src/foo.py:42:42: error: ..."
_LINE_ERROR_RE = re.compile(
    r"^(?P<file>[^\s:]+\.py):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<error>.*)$",
    re.MULTILINE,
)

# Short summary: "FAILED src/foo.py::test_name - ..."
_SHORT_FAILURE_RE = re.compile(
    r"^FAILED\s+(?P<file>[^\s:]+\.py)::(?P<test>[^\s]+)\s+-\s+(?P<error>.+)$",
    re.MULTILINE,
)


def parse_pytest_output(text: str) -> list[dict[str, Any]]:
    """Extract structured failures from pytest/ruff style output.

    Returns a list of dicts with keys: file, line (optional), test (optional),
    error. Duplicates are removed while preserving order.
    """
    if not text:
        return []

    seen: set[str] = set()
    failures: list[dict[str, Any]] = []

    def add(record: dict[str, Any]) -> None:
        key = "|".join(
            str(record.get(k, "")) for k in ("file", "line", "test", "error")
        )
        if key not in seen:
            seen.add(key)
            failures.append(record)

    for match in _PYTEST_FAILURE_RE.finditer(text):
        add(
            {
                "file": match.group("file").strip(),
                "test": match.group("test").strip(),
                "error": match.group("error").strip(),
            }
        )

    for match in _SHORT_FAILURE_RE.finditer(text):
        add(
            {
                "file": match.group("file").strip(),
                "test": match.group("test").strip(),
                "error": match.group("error").strip(),
            }
        )

    for match in _LINE_ERROR_RE.finditer(text):
        error = match.group("error").strip()
        # Filter out ruff summary lines like "Found 2 errors."
        if not error or "Found" in error and "error" in error.lower():
            continue
        record: dict[str, Any] = {
            "file": match.group("file").strip(),
            "error": error,
        }
        line = match.group("line")
        if line:
            record["line"] = int(line)
        col = match.group("col")
        if col:
            record["col"] = int(col)
        add(record)

    return failures


class ParseTestOutputTool(ToolPlugin):
    """Parse pytest/ruff output into a structured list of failures."""

    name = "parse_test_output"
    description = (
        "Parse the stdout/stderr of a test or lint command and return structured "
        "failure records with file, line, and error message. Useful for diagnosing "
        "test failures without reading the entire raw output."
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=True,
            side_effects="none",
            default_timeout_ms=5_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "output": {
                    "type": "string",
                    "description": "Raw stdout/stderr from pytest or ruff.",
                },
            },
            "required": ["output"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        output = params.get("output")
        if not isinstance(output, str):
            raise ToolError("output must be a string", tool_name=self.name)
        failures = parse_pytest_output(output)
        return {
            "failures": failures,
            "count": len(failures),
            "message": (
                f"Found {len(failures)} failure(s)."
                if failures
                else "No failures detected."
            ),
        }
