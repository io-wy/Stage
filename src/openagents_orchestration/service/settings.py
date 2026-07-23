"""Shared settings for Stage application services."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVALS_JSON = REPO_ROOT / "skills" / "case-handling-baseline" / "evals" / "evals.json"
DEFAULT_BASELINE_WORKSPACE = (
    REPO_ROOT / "skills" / "case-handling-baseline-workspace" / "hard-v2-baseline"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "docs" / "reports" / "stage-web-console"
DEFAULT_FEEDBACK_ROOT = REPO_ROOT / "docs" / "reports" / "stage-feedback"
DEFAULT_WIKI_PATH = Path("/Users/io/Downloads/wiki/SAST 设施指南")

