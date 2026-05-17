"""Shell command execution with regex denylist + per-session cwd tracking.

Faithful port of CoreCoder's bash tool. Differences from the original:

- The 9 dangerous-pattern regexes are kept verbatim.
- The ``_cwd`` module global is replaced with a per-session value stored in
  ``context.scratch["bash_cwd"]`` so concurrent sessions do not clobber each
  other.
- Output truncation keeps a generous head and a small tail (the original
  6000/3000 split) so error tails (last lines of a stack trace) survive.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)"), "recursive delete on home/root"),
    (re.compile(r"\brm\s+(-\w*)?-rf\s"), "force recursive delete"),
    (re.compile(r"\bmkfs\b"), "format filesystem"),
    (re.compile(r"\bdd\s+.*of=/dev/"), "raw disk write"),
    (re.compile(r">\s*/dev/sd[a-z]"), "overwrite block device"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\s+/"), "chmod 777 on root"),
    (re.compile(r":\(\)\s*\{.*:\|:.*\}"), "fork bomb"),
    (re.compile(r"\bcurl\b.*\|\s*(sudo\s+)?bash"), "pipe curl to bash"),
    (re.compile(r"\bwget\b.*\|\s*(sudo\s+)?bash"), "pipe wget to bash"),
]

_OUTPUT_HARD_LIMIT = 15_000
_OUTPUT_HEAD = 6_000
_OUTPUT_TAIL = 3_000
_DEFAULT_TIMEOUT_S = 120


class BashTool(ToolPlugin):
    """Run a shell command with regex denylist and cwd memory."""

    name = "bash"
    description = (
        "Execute a shell command. Returns stdout, stderr, and exit code. "
        "Use for tests, builds, git operations, package installs. "
        "Dangerous patterns (rm -rf /, fork bombs, curl|bash, etc.) are blocked."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=_DEFAULT_TIMEOUT_S * 1_000,
            interrupt_behavior="cancel",
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120).",
                    "minimum": 1,
                    "maximum": 600,
                },
                "description": {
                    "type": "string",
                    "description": "Short note for telemetry; not passed to the shell.",
                },
            },
            "required": ["command"],
        }

    async def invoke(
        self, params: dict[str, Any], context: "RunContext[Any] | None"
    ) -> dict[str, Any]:
        command = str(params.get("command", "")).strip()
        if not command:
            raise ToolError("command is required", tool_name=self.name)
        timeout = int(params.get("timeout", _DEFAULT_TIMEOUT_S) or _DEFAULT_TIMEOUT_S)

        warning = _check_dangerous(command)
        if warning:
            return {
                "command": command,
                "blocked": True,
                "reason": warning,
                "stdout": "",
                "stderr": "",
                "exit_code": None,
                "message": (
                    f"⚠ Blocked: {warning}\nCommand: {command}\n"
                    "If this was intentional, narrow the command (e.g. use a specific path)."
                ),
            }

        cwd = _get_cwd(context)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return {
                "command": command,
                "blocked": False,
                "exit_code": None,
                "timed_out": True,
                "stdout": "",
                "stderr": "",
                "message": f"Error: command timed out after {timeout}s",
            }
        except (OSError, ValueError) as exc:
            raise ToolError(f"Failed to launch command: {exc}", tool_name=self.name) from exc

        if proc.returncode == 0:
            _update_cwd(context, command, cwd)

        out = proc.stdout or ""
        if proc.stderr:
            out += f"\n[stderr]\n{proc.stderr}"
        if proc.returncode != 0:
            out += f"\n[exit code: {proc.returncode}]"
        if len(out) > _OUTPUT_HARD_LIMIT:
            out = (
                out[:_OUTPUT_HEAD]
                + f"\n\n... truncated ({len(out)} chars total) ...\n\n"
                + out[-_OUTPUT_TAIL:]
            )

        return {
            "command": command,
            "blocked": False,
            "exit_code": proc.returncode,
            "cwd": cwd,
            "timed_out": False,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "message": out.strip() or "(no output)",
        }


def _check_dangerous(cmd: str) -> str | None:
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            return reason
    return None


def _get_cwd(context: "RunContext[Any] | None") -> str:
    if context is not None:
        cached = context.scratch.get("bash_cwd")
        if isinstance(cached, str) and os.path.isdir(cached):
            return cached
    return os.getcwd()


def _update_cwd(context: "RunContext[Any] | None", command: str, current_cwd: str) -> None:
    if context is None:
        return
    parts = command.split("&&")
    new_dir = current_cwd
    for raw in parts:
        part = raw.strip()
        if part.startswith("cd "):
            target = part[3:].strip().strip("'\"")
            if not target:
                continue
            candidate = os.path.normpath(
                os.path.join(new_dir, os.path.expanduser(target))
            )
            if os.path.isdir(candidate):
                new_dir = candidate
    if new_dir != current_cwd:
        context.scratch["bash_cwd"] = new_dir
