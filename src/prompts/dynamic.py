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
    plan: dict[str, Any] | None = None,
    exploration_cache: dict[str, Any] | None = None,
    pending_verification: dict[str, Any] | None = None,
    recent_thoughts: list[str] | None = None,
    git_commits: list[str] | None = None,
    git_diff_stat: str | None = None,
) -> str:
    """Render the per-run fragment appended after the core principles.

    The fragment is split into categorized sections separated by
    ``__CATEGORY_BOUNDARY__``. ``CoreCoderPattern`` can turn each category
    into its own system message, improving prefix caching and making it
    easier for the model to locate information.
    """
    sections: list[str] = []
    cwd = cwd or os.getcwd()

    # --- Environment ---
    env_lines: list[str] = ["# Working environment"]
    env_lines.append(f"- cwd: {cwd}")
    git_line = _git_status_line(cwd)
    if git_line:
        env_lines.append(f"- git: {git_line}")
    if git_commits:
        shown = " | ".join(git_commits[:3])
        env_lines.append(f"- recent commits: {shown}")
    if git_diff_stat:
        env_lines.append("- uncommitted diff:\n" + _indent(git_diff_stat, "    "))
    project = _detect_project_type(cwd)
    if project:
        env_lines.append(f"- project: {project['type']}")
        if project.get("test_cmd"):
            env_lines.append(f"  test command: `{project['test_cmd']}`")
        if project.get("lint_cmd"):
            env_lines.append(f"  lint command: `{project['lint_cmd']}`")
    if tool_names:
        env_lines.append(f"- tools available: {', '.join(sorted(tool_names))}")
    sections.append("\n".join(env_lines))

    # --- Memory ---
    memory_lines: list[str] = ["# Session memory"]
    if dirty_files:
        rendered = list(dirty_files)
        rendered.sort()
        if len(rendered) > 8:
            shown = rendered[:8]
            tail = f", ... (+{len(rendered) - 8} more)"
        else:
            shown = rendered
            tail = ""
        memory_lines.append(f"- modified this session: {', '.join(shown)}{tail}")
    if todo_list:
        counts: dict[str, int] = {}
        for t in todo_list:
            counts[t.get("status", "pending")] = counts.get(t.get("status", "pending"), 0) + 1
        total = len(todo_list)
        done = counts.get("completed", 0)
        active = counts.get("in_progress", 0)
        memory_lines.append(f"- todo progress: {done}/{total} done, {active} in_progress")
    if len(memory_lines) > 1:
        sections.append("\n".join(memory_lines))

    # --- Plan ---
    plan_fragment = build_plan_fragment(plan)
    if plan_fragment:
        sections.append(plan_fragment)

    # --- Exploration cache ---
    cache_fragment = build_exploration_cache_fragment(exploration_cache)
    if cache_fragment:
        sections.append(cache_fragment)

    # --- Verification ---
    verification_fragment = build_verification_fragment(pending_verification)
    if verification_fragment:
        sections.append(verification_fragment)

    # --- Recent reasoning ---
    thoughts_fragment = build_thoughts_fragment(recent_thoughts)
    if thoughts_fragment:
        sections.append(thoughts_fragment)

    return "\n\n__CATEGORY_BOUNDARY__\n\n".join(sections)


def gather_runtime_context(ctx: RunContext[Any]) -> dict[str, Any]:
    """Pull the bits of state used by :func:`build_runtime_fragment`.

    Returns a dict so callers can pass kwargs straight in. Defensive against
    partial/fake contexts used in tests.
    """
    scratch = getattr(ctx, "scratch", {}) or {}
    state = getattr(ctx, "state", {}) or {}
    tools = getattr(ctx, "tools", {}) or {}

    cwd = scratch.get("bash_cwd")
    if not isinstance(cwd, str) or not Path(cwd).exists():
        cwd = os.getcwd()
    dirty = scratch.get("dirty_files")
    if isinstance(dirty, set):
        dirty_list: list[str] = sorted(dirty)
    elif isinstance(dirty, list):
        dirty_list = list(dirty)
    else:
        dirty_list = []
    tool_names = list(tools.keys()) if tools else []
    todo_list = scratch.get("todo_list")
    if not isinstance(todo_list, list):
        todo_list = None

    plan = state.get("__plan__")
    exploration_cache = {
        "file": scratch.get("_file_cache", {}),
        "glob": scratch.get("_glob_cache", {}),
        "grep": scratch.get("_grep_cache", {}),
        "list_directory": scratch.get("_list_dir_cache", {}),
    }
    pending_verification = state.get("__pending_verification__")
    recent_thoughts = scratch.get("_recent_thoughts")
    if not isinstance(recent_thoughts, list):
        recent_thoughts = None

    return {
        "cwd": cwd,
        "dirty_files": dirty_list,
        "tool_names": tool_names,
        "todo_list": todo_list,
        "plan": plan if isinstance(plan, dict) else None,
        "exploration_cache": exploration_cache,
        "pending_verification": pending_verification if isinstance(pending_verification, dict) else None,
        "recent_thoughts": recent_thoughts,
        "git_commits": _git_recent_commits(cwd),
        "git_diff_stat": _git_diff_stat(cwd),
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


def _git_recent_commits(cwd: str, n: int = 3) -> list[str] | None:
    """Return the last n commit one-liners, or None if git is unavailable."""
    if not Path(cwd, ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "log", f"-{n}", "--oneline", "--no-decorate"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    return lines or None


def _git_diff_stat(cwd: str) -> str | None:
    """Return a short diff --stat for dirty files, or None if clean/no git."""
    if not Path(cwd, ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.rstrip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    if len(lines) > 5:
        lines = lines[:5] + [f"... ({len(lines) - 5} more files)"]
    return "\n".join(lines)


def build_plan_fragment(plan: dict[str, Any] | None) -> str:
    """Render the agent's current execution plan."""
    if not plan:
        return ""
    lines: list[str] = ["# Current plan"]
    steps = plan.get("steps")
    if isinstance(steps, list) and steps:
        for i, s in enumerate(steps, 1):
            lines.append(f"{i}. {s}")
    files_to_read = plan.get("files_to_read")
    if isinstance(files_to_read, list) and files_to_read:
        lines.append(f"- files to read: {', '.join(str(f) for f in files_to_read)}")
    files_to_edit = plan.get("files_to_edit")
    if isinstance(files_to_edit, list) and files_to_edit:
        lines.append(f"- files to edit/create: {', '.join(str(f) for f in files_to_edit)}")
    tests_to_run = plan.get("tests_to_run")
    if isinstance(tests_to_run, list) and tests_to_run:
        lines.append(f"- tests to run: {', '.join(str(t) for t in tests_to_run)}")
    return "\n".join(lines)


def build_exploration_cache_fragment(cache: dict[str, Any] | None) -> str:
    """Render recently explored files so the agent avoids redundant reads."""
    if not cache:
        return ""
    file_cache = cache.get("file", {}) if isinstance(cache, dict) else {}
    glob_cache = cache.get("glob", {}) if isinstance(cache, dict) else {}
    grep_cache = cache.get("grep", {}) if isinstance(cache, dict) else {}
    list_dir_cache = cache.get("list_directory", {}) if isinstance(cache, dict) else {}

    if not file_cache and not glob_cache and not grep_cache and not list_dir_cache:
        return ""

    lines: list[str] = ["# Files already read this session"]
    for path, info in sorted(file_cache.items())[-12:]:
        lines.append(f"- {path} ({info.get('lines', '?')} lines)")
    if list_dir_cache:
        paths = list(list_dir_cache.keys())[-6:]
        lines.append(f"- recent directory listings: {', '.join(str(p) for p in paths)}")
    if glob_cache:
        patterns = list(glob_cache.keys())[-6:]
        lines.append(f"- recent globs: {', '.join(str(p) for p in patterns)}")
    if grep_cache:
        patterns = list(grep_cache.keys())[-6:]
        lines.append(f"- recent greps: {', '.join(str(p) for p in patterns)}")
    return "\n".join(lines)


def build_verification_fragment(pending: dict[str, Any] | None) -> str:
    """Render a nudge when the agent just edited files and should verify."""
    if not pending:
        return ""
    files = pending.get("files", [])
    tool = pending.get("tool", "")
    lines: list[str] = ["# Pending verification"]
    lines.append(
        f"You just used `{tool}` on {', '.join(str(f) for f in files)}. "
        "Run the relevant tests or lint now. If they fail, diagnose and fix before continuing."
    )
    return "\n".join(lines)


def build_thoughts_fragment(thoughts: list[str] | None) -> str:
    """Render recent reasoning steps from the `think` tool."""
    if not thoughts:
        return ""
    recent = thoughts[-3:]
    if not recent:
        return ""
    lines = ["# Recent reasoning"]
    for i, t in enumerate(recent, 1):
        lines.append(f"{i}. {t[:250]}{'...' if len(t) > 250 else ''}")
    return "\n".join(lines)


def _indent(text: str, prefix: str) -> str:
    """Indent every line of a multi-line string."""
    return "\n".join(prefix + line for line in text.splitlines())


def _detect_project_type(cwd: str) -> dict[str, str] | None:
    """Detect project type and common commands from marker files."""
    root = Path(cwd)
    markers: list[tuple[str, str, str, str]] = [
        ("pyproject.toml", "Python", "uv run pytest", "uv run ruff check"),
        ("setup.py", "Python", "pytest", "flake8"),
        ("requirements.txt", "Python", "pytest", "flake8"),
        ("package.json", "Node.js", "npm test", "npm run lint"),
        ("Cargo.toml", "Rust", "cargo test", "cargo clippy"),
        ("go.mod", "Go", "go test ./...", "golangci-lint run"),
        ("pom.xml", "Java/Maven", "mvn test", "mvn verify"),
        ("build.gradle", "Java/Gradle", "gradle test", "gradle check"),
        ("Gemfile", "Ruby", "bundle exec rspec", "bundle exec rubocop"),
    ]
    for marker, ptype, test_cmd, lint_cmd in markers:
        if (root / marker).exists():
            return {"type": ptype, "test_cmd": test_cmd, "lint_cmd": lint_cmd}
    return None
