"""Per-run dynamic prompt fragments.

These are appended after the static system prompt via __DYNAMIC_BOUNDARY__
so the static portion can be prefix-cached across turns.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from openagents.interfaces.run_context import RunContext


def build_runtime_fragment(
    *,
    cwd: str | None = None,
    dirty_files: set[str] | list[str] | None = None,
    tool_names: list[str] | None = None,
    todo_list: list[dict[str, Any]] | None = None,
) -> str:
    """Render the per-run fragment appended after the core principles.

    Kept short on purpose — the static principles are already in the system
    prompt, and adding too much per-run text defeats prefix caching.
    """
    parts: list[str] = ["# Working environment"]
    cwd = cwd or os.getcwd()
    parts.append(f"- cwd: {cwd}")

    git_line = _git_status_line(cwd)
    if git_line:
        parts.append(f"- git: {git_line}")

    if tool_names:
        parts.append(f"- tools available: {', '.join(sorted(tool_names))}")

    if dirty_files:
        rendered = list(dirty_files)
        rendered.sort()
        if len(rendered) > 8:
            shown = rendered[:8]
            tail = f", ... (+{len(rendered) - 8} more)"
        else:
            shown = rendered
            tail = ""
        parts.append(f"- modified this session: {', '.join(shown)}{tail}")

    # Todo progress reminder (Claude Code pattern)
    if todo_list:
        counts: dict[str, int] = {}
        for t in todo_list:
            counts[t.get("status", "pending")] = counts.get(t.get("status", "pending"), 0) + 1
        total = len(todo_list)
        done = counts.get("completed", 0)
        active = counts.get("in_progress", 0)
        parts.append(f"- todo progress: {done}/{total} done, {active} in_progress")

    return "\n".join(parts)


def gather_runtime_context(ctx: RunContext[Any]) -> dict[str, Any]:
    """Pull the bits of state used by :func:`build_runtime_fragment`.

    Returns a dict so callers can pass kwargs straight in.
    """
    cwd = ctx.scratch.get("bash_cwd")
    if not isinstance(cwd, str) or not Path(cwd).exists():
        cwd = os.getcwd()
    dirty = ctx.scratch.get("dirty_files")
    if isinstance(dirty, set):
        dirty_list: list[str] = sorted(dirty)
    elif isinstance(dirty, list):
        dirty_list = list(dirty)
    else:
        dirty_list = []
    tool_names = list(ctx.tools.keys()) if ctx.tools else []
    todo_list = ctx.scratch.get("todo_list")
    if not isinstance(todo_list, list):
        todo_list = None
    return {
        "cwd": cwd,
        "dirty_files": dirty_list,
        "tool_names": tool_names,
        "todo_list": todo_list,
    }


def _git_status_line(cwd: str) -> str | None:
    """Return a one-line git summary, or None if not a git repo / git missing."""
    if not Path(cwd, ".git").exists():
        return None
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    branch_name = (branch.stdout or "").strip() or "(detached)"
    dirty_lines = [ln for ln in (status.stdout or "").splitlines() if ln.strip()]
    if not dirty_lines:
        return f"branch={branch_name}, clean"
    sample = dirty_lines[:3]
    suffix = f", ... (+{len(dirty_lines) - 3} more)" if len(dirty_lines) > 3 else ""
    return f"branch={branch_name}, dirty=[{'; '.join(sample)}{suffix}]"
