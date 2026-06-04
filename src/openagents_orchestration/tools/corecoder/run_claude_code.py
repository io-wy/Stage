"""run_claude_code — invoke the local Claude Code CLI as a sub-process.

Useful when the agent encounters tasks that are awkward with its own
read/write/edit/bash toolkit: complex multi-file refactors, environment
setup, dependency debugging, etc.

Runs in non-interactive mode (``claude -p``) so the sub-process exits
automatically after emitting its response.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from openagents.errors.exceptions import PermanentToolError, ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

_DEFAULT_TIMEOUT_S = 300
_DEFAULT_ALLOWED_TOOLS = ["Read", "Edit", "Bash", "Glob", "Grep"]


class RunClaudeCodeTool(ToolPlugin):
    """Invoke the local ``claude`` CLI in non-interactive mode."""

    name = "run_claude_code"
    description = (
        "Invoke the local Claude Code CLI (claude -p) to perform complex tasks "
        "that are hard to do with the agent's own toolkit. "
        "Good for: multi-file refactors, environment setup, dependency debugging, "
        "complex test fixes. The sub-process runs non-interactively and returns its output. "
        "IMPORTANT: This consumes its own API tokens — use sparingly for tasks the agent "
        "cannot solve with read_file/write_file/edit_file/bash."
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
                "instruction": {
                    "type": "string",
                    "description": (
                        "Natural-language instruction for Claude Code. Be specific: "
                        "mention file paths, what to change, and constraints."
                    ),
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional file paths to pass to Claude Code as context. "
                        "Claude Code will read these automatically."
                    ),
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Comma-separated list of tool names Claude Code is allowed to use. "
                        f"Default: {', '.join(_DEFAULT_ALLOWED_TOOLS)}. "
                        "Restrict to the minimum needed to avoid unexpected side effects."
                    ),
                },
                "skip_permissions": {
                    "type": "boolean",
                    "description": (
                        "If true, passes --dangerously-skip-permissions to skip all "
                        "confirmation prompts. ONLY use in sandboxed environments."
                    ),
                    "default": False,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 300.",
                    "default": _DEFAULT_TIMEOUT_S,
                },
            },
            "required": ["instruction"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        instruction = str(params.get("instruction", "")).strip()
        if not instruction:
            raise ToolError("instruction is required", tool_name=self.name)

        claude_path = shutil.which("claude")
        if claude_path is None:
            raise PermanentToolError(
                "claude CLI not found in PATH. Install Claude Code first: "
                "https://docs.anthropic.com/en/docs/claude-code/setup",
                tool_name=self.name,
            )

        files = params.get("files") or []
        allowed_tools = params.get("allowed_tools") or _DEFAULT_ALLOWED_TOOLS
        skip_permissions = bool(params.get("skip_permissions", False))
        timeout = int(params.get("timeout", _DEFAULT_TIMEOUT_S))

        cmd: list[str] = [claude_path, "-p"]

        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if allowed_tools:
            cmd.append(f"--allowedTools={','.join(allowed_tools)}")

        # Add files as positional arguments (claude reads them as context)
        valid_files: list[str] = []
        for f in files:
            p = Path(str(f))
            if p.exists():
                valid_files.append(str(p))
            else:
                # Warn but don't fail — the instruction may reference files
                # that claude-code can find on its own
                pass

        # Instruction goes last as the prompt argument
        cmd.append(instruction)
        cmd.extend(valid_files)

        # Run in the current working directory (agent's CWD)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(Path.cwd()),
            )
        except subprocess.TimeoutExpired as exc:
            def _decode_stream(val: bytes | str | None) -> str:
                if val is None:
                    return ""
                if isinstance(val, bytes):
                    return val.decode("utf-8", errors="replace")
                return str(val)

            stdout = _decode_stream(exc.stdout)[:2000]
            stderr = _decode_stream(exc.stderr)[:2000]
            raise ToolError(
                f"claude-code timed out after {timeout}s.\nstdout: {stdout}\nstderr: {stderr}",
                tool_name=self.name,
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"Failed to run claude-code: {exc}",
                tool_name=self.name,
            ) from exc

        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"

        # Truncate very long output
        max_output = 8_000
        if len(output) > max_output:
            output = output[:max_output - 200] + "\n... (output truncated)"

        return {
            "exit_code": result.returncode,
            "output": output,
            "command": " ".join(cmd),
            "message": (
                f"claude-code exited with code {result.returncode}.\n\n{output}"
            ),
        }
