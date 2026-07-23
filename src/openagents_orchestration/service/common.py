"""Shared helpers for Stage application services."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openagents_orchestration.service.settings import (
    DEFAULT_OUTPUT_ROOT,
    REPO_ROOT,
    WIKI_PATH_ENV_VAR,
    configured_wiki_path,
)


def extract_case_prompt(prompt: str) -> str:
    marker = "Case:"
    if marker not in prompt:
        return prompt.strip()
    case_text = prompt.split(marker, 1)[1].strip()
    for stop_marker in ["Use only", "Produce the required"]:
        if stop_marker in case_text:
            case_text = case_text.split(stop_marker, 1)[0].strip()
    return case_text or prompt.strip()


def run_key(governance_path: Path) -> str:
    try:
        relative = governance_path.relative_to(DEFAULT_OUTPUT_ROOT)
    except ValueError:
        relative = governance_path
    return hashlib.sha1(str(relative).encode("utf-8")).hexdigest()[:16]


def text_digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def resolve_path(value: str | Path | None, default: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def resolve_required_path(value: str | Path) -> Path:
    path = resolve_path(value, Path())
    if not path.exists():
        raise FileNotFoundError(f"path not found: {path}")
    return path


def resolve_wiki_path(value: str | Path | None) -> Path:
    if value is not None and str(value).strip():
        path = resolve_path(value, Path())
    else:
        path = configured_wiki_path()
        if path is None:
            raise ValueError(f"wiki_path is required or {WIKI_PATH_ENV_VAR} must be set")
    if not path.exists():
        raise FileNotFoundError(f"path not found: {path}")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
