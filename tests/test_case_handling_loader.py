"""Tests for the case-handling benchmark loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.case_handling.loader import CaseLoadError, load_case_specs


def _write_case(path: Path, *, case_id: str, family: str, difficulty: str) -> None:
    path.write_text(
        f"""
case_id: {case_id}
family: {family}
source: SAST 设施指南
objective: Answer a question.
context_pack:
  sources:
    - doc.md
success_criteria:
  - Must answer the question.
verifier:
  type: manual
difficulty: {difficulty}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_load_case_specs_supports_filters_and_errors(tmp_path: Path) -> None:
    root = tmp_path / "cases"
    root.mkdir()
    _write_case(root / "b.yaml", case_id="case-b", family="approval", difficulty="hard")
    _write_case(root / "a.yaml", case_id="case-a", family="knowledge", difficulty="easy")
    (root / "bad.yaml").write_text(
        """
case_id: case-bad
family: knowledge
source: SAST 设施指南
objective: Missing success criteria.
context_pack:
  sources: []
verifier:
  type: manual
difficulty: easy
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors: list[CaseLoadError] = []
    cases = load_case_specs(root, errors_out=errors)

    assert [case.case_id for case in cases] == ["case-a", "case-b"]
    assert len(errors) == 1
    assert errors[0].path.name == "bad.yaml"
    assert "success" in errors[0].message.lower()

    clean_root = tmp_path / "clean_cases"
    clean_root.mkdir()
    _write_case(
        clean_root / "b.yaml", case_id="case-b", family="approval", difficulty="hard"
    )
    _write_case(
        clean_root / "a.yaml", case_id="case-a", family="knowledge", difficulty="easy"
    )

    filtered = load_case_specs(clean_root, family="approval", difficulty="hard")
    assert [case.case_id for case in filtered] == ["case-b"]

    limited = load_case_specs(clean_root, limit=1)
    assert len(limited) == 1
    assert limited[0].case_id == "case-a"


def test_load_case_specs_raises_without_error_sink(tmp_path: Path) -> None:
    root = tmp_path / "cases"
    root.mkdir()
    (root / "bad.yaml").write_text(
        """
case_id: case-bad
family: knowledge
source: SAST 设施指南
objective: Missing success criteria.
context_pack:
  sources: []
verifier:
  type: manual
difficulty: easy
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bad.yaml"):
        load_case_specs(root)
