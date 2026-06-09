"""Base helpers for GitHub CLI tools."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from typing import Any

from openagents.errors.exceptions import ToolError

_GH_NOT_FOUND_MSG = (
    "GitHub CLI (`gh`) is not installed or not in PATH. "
    "Install it from https://cli.github.com/ and run `gh auth login`."
)

_GH_NOT_AUTH_MSG = (
    "Not authenticated with GitHub. Run `gh auth login` first."
)


def _check_gh() -> str | None:
    """Return error message if gh is not available, None if OK."""
    if shutil.which("gh") is None:
        return _GH_NOT_FOUND_MSG
    # Quick auth check
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return _GH_NOT_AUTH_MSG
    except (subprocess.TimeoutExpired, OSError):
        return _GH_NOT_FOUND_MSG
    return None


def _run_gh(
    args: list[str],
    *,
    repo: str | None = None,
    timeout: int = 60,
    check: bool = False,
) -> dict[str, Any]:
    """Run a gh CLI command and return structured output.

    Args:
        args: Command arguments (after 'gh').
        repo: Optional 'owner/repo' to set via GH_REPO env.
        timeout: Timeout in seconds.
        check: If True, raise ToolError on non-zero exit.

    Returns:
        Dict with stdout, stderr, exit_code, parsed_json (if applicable).
    """
    err = _check_gh()
    if err:
        return {
            "success": False,
            "error": err,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
        }

    cmd = ["gh", *args]
    env = None
    if repo:
        import os
        env = {**os.environ, "GH_REPO": repo}

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout}s: {' '.join(cmd)}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
        }
    except (OSError, ValueError) as exc:
        raise ToolError(f"Failed to run gh: {exc}", tool_name="github") from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if check and proc.returncode != 0:
        err_msg = stderr.strip() or stdout.strip() or f"exit code {proc.returncode}"
        raise ToolError(f"gh command failed: {err_msg}", tool_name="github")

    # Try to parse JSON output
    parsed = None
    if stdout.strip():
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout)

    return {
        "success": proc.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": proc.returncode,
        "parsed": parsed,
    }


def _format_result(
    result: dict[str, Any],
    *,
    summary: str = "",
    max_chars: int = 8000,
) -> dict[str, Any]:
    """Format gh output for LLM consumption."""
    if not result["success"]:
        return {
            "success": False,
            "error": result.get("error") or result.get("stderr", "").strip(),
            "message": result.get("error") or result.get("stderr", "").strip(),
        }

    data = result.get("parsed")
    if data is not None:
        # Structured JSON output
        payload = {
            "success": True,
            "data": data,
        }
        if summary:
            payload["summary"] = summary
        return payload

    # Plain text output
    text = result.get("stdout", "").strip()
    if len(text) > max_chars:
        text = text[: max_chars - 100] + f"\n... ({len(text)} chars total) ..."

    return {
        "success": True,
        "output": text,
        "message": summary or text[:500],
    }
