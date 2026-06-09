"""Deterministic verification summary for orchestration reports."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def build_verification_report(board: Any, *, work_dir: str | Path | None = None) -> dict[str, Any]:
    """Build a non-LLM verification view for the current run/workspace."""
    root = Path(work_dir) if work_dir is not None else Path.cwd()
    verified_artifacts = sorted(
        path for path, rec in board.artifacts.items() if rec.status == "verified"
    )
    missing_artifacts = sorted(
        path for path, rec in board.artifacts.items() if rec.status == "missing"
    )
    changed_files = _git_changed_files(root)
    test_files = _test_files(root)
    migration_files = _migration_files(root)
    project_context = board.get_project_context()

    return {
        "work_dir": str(root),
        "verified_artifacts": verified_artifacts[-100:],
        "missing_artifacts": missing_artifacts[-100:],
        "git_changed_files": changed_files[-100:],
        "test_files_present": test_files[-100:],
        "migration_files_present": migration_files[-100:],
        "latest_test_report": project_context.get("latest_test_report"),
        "recent_errors": project_context.get("recent_errors", []),
        "signals": {
            "has_verified_artifacts": bool(verified_artifacts),
            "has_missing_artifacts": bool(missing_artifacts),
            "has_tests": bool(test_files),
            "has_migrations": bool(migration_files),
            "has_uncommitted_changes": bool(changed_files),
        },
    }


def _git_changed_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    files: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) > 3:
            files.append(line[3:])
    return files


def _test_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [str(path) for path in root.rglob("test_*.py") if ".venv" not in path.parts]


def _migration_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [str(path) for path in root.rglob("alembic/versions/*.py")]
