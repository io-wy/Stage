"""Tests for project type detection in dynamic prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompts.dynamic import _detect_project_type


@pytest.mark.parametrize(
    "marker,expected_type,expected_test",
    [
        ("pyproject.toml", "Python", "uv run pytest"),
        ("package.json", "Node.js", "npm test"),
        ("Cargo.toml", "Rust", "cargo test"),
        ("go.mod", "Go", "go test ./..."),
    ],
)
def test_detect_project_type(tmp_path: Path, marker: str, expected_type: str, expected_test: str):
    (tmp_path / marker).write_text("{}")
    result = _detect_project_type(str(tmp_path))
    assert result is not None
    assert result["type"] == expected_type
    assert result["test_cmd"] == expected_test


def test_detect_project_type_unknown(tmp_path: Path):
    result = _detect_project_type(str(tmp_path))
    assert result is None
