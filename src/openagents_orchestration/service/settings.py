"""Shared settings for Stage application services."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVALS_JSON = REPO_ROOT / "skills" / "case-handling-baseline" / "evals" / "evals.json"
DEFAULT_BASELINE_WORKSPACE = (
    REPO_ROOT / "skills" / "case-handling-baseline-workspace" / "hard-v2-baseline"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "docs" / "reports" / "stage-web-console"
DEFAULT_FEEDBACK_ROOT = REPO_ROOT / "docs" / "reports" / "stage-feedback"
WIKI_PATH_ENV_VAR = "STAGE_WIKI_PATH"


def configured_wiki_path() -> Path | None:
    value = os.environ.get(WIKI_PATH_ENV_VAR, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path
