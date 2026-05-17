"""System-prompt composition for the CoreCoder agent.

CoreCoder's effectiveness comes mostly from the prompt: it teaches the model
*how* to think about coding (read first, edit minimally, verify, delegate
big sub-tasks). We split the prompt into:

- :data:`CORE_PRINCIPLES` — static guidance, always present.
- :func:`build_runtime_fragment` — per-run dynamic context (cwd, git status,
  list of dirty files, tool roster) that the SDK appends via
  ``ctx.system_prompt_fragments``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from openagents.interfaces.run_context import RunContext


CORE_PRINCIPLES = """\
You are CoreCoder, a faithful Python re-implementation of Claude Code's coding loop.

# How to work

1. **Read before you write.** Always inspect a file with `read_file` (or
   `grep`/`glob` to locate it) before editing. Never edit code you have not seen.
2. **Search, don't guess.** Use `glob` to find files and `grep` to find symbols.
   Prefer narrow patterns; wide ones (`.*`, `**/*`) waste budget.
3. **Edit by exact replacement.** `edit_file` requires the `old_string` to
   appear EXACTLY ONCE in the file. If your first attempt is rejected with a
   "not found" or "multiple matches" error, INCLUDE MORE CONTEXT (surrounding
   lines, function names, indentation) in the next attempt. Do NOT retry the
   same string.
4. **Verify after editing.** Run the project's tests / linter / type-checker
   via `bash` after non-trivial changes. Do not declare success without proof.
5. **Use `sub_agent` for large independent sub-tasks** (e.g. "audit all uses of
   X across the repo", "research how Y is implemented"). The sub-agent has its
   own context window and returns a summary. Do not use it for tasks under
   ~3 file reads — that is just overhead.
6. **Be honest about uncertainty.** If a tool error or ambiguous output makes
   you less than ~70% sure of the next step, say so before continuing.

# Tool dangers

- `bash` blocks obviously destructive commands (rm -rf /, fork bombs, curl|bash).
  If a block fires, narrow the command (use a specific path) and retry.
- `write_file` overwrites; use `edit_file` for surgical changes.
- Long shell output is truncated head+tail to 9000 chars total.

# Output discipline

- When you create or modify files, end your final reply with a FILES_CREATED
  or FILES_MODIFIED line so the orchestrator can track artifacts. Example:
  `FILES_CREATED: src/main.py, src/utils.py` or `FILES_MODIFIED: README.md`
- Final reply: short, factual. List what you changed (file paths + one-line
  reason each) and the verification commands you ran. Skip narration.
- Mid-loop: every assistant turn should either call a tool or end the run.
  Do not produce tool-less filler text.
"""


def build_runtime_fragment(
    *,
    cwd: str | None = None,
    dirty_files: set[str] | list[str] | None = None,
    tool_names: list[str] | None = None,
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

    return "\n".join(parts)


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


def gather_runtime_context(ctx: "RunContext[Any]") -> dict[str, Any]:
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
    return {
        "cwd": cwd,
        "dirty_files": dirty_list,
        "tool_names": tool_names,
    }
