"""Deterministic hard-gate verifier for case-handling outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.case_handling.schema import CaseSpec


@dataclass(frozen=True, slots=True)
class CaseVerificationResult:
    """Result of deterministic case verification."""

    passed: bool
    scores: dict[str, float]
    errors: dict[str, str]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


def _score_required(
    *,
    expected: list[str],
    actual: list[str],
    key: str,
    scores: dict[str, float],
    errors: dict[str, str],
) -> None:
    if not expected:
        return
    actual_set = set(actual)
    missing = [item for item in expected if item not in actual_set]
    scores[key] = 0.0 if missing else 1.0
    if missing:
        errors[key] = f"missing: {', '.join(missing)}"


def verify_case_output(
    case: CaseSpec,
    output: dict[str, Any],
    *,
    work_dir: str | Path | None = None,
) -> CaseVerificationResult:
    """Verify a case output against deterministic hard gates."""

    verifier = case.verifier
    scores: dict[str, float] = {}
    errors: dict[str, str] = {}

    closed = output.get("closed") is True
    scores["closed"] = 1.0 if closed else 0.0
    if not closed:
        errors["closed"] = "case output is not marked closed"

    required_evidence = _as_list(verifier.get("required_evidence"))
    evidence = _as_list(output.get("evidence"))
    _score_required(
        expected=required_evidence,
        actual=evidence,
        key="required_evidence",
        scores=scores,
        errors=errors,
    )

    required_text = _as_list(verifier.get("required_text"))
    answer = str(output.get("answer", ""))
    if required_text:
        missing_text = [item for item in required_text if item not in answer]
        scores["required_text"] = 0.0 if missing_text else 1.0
        if missing_text:
            errors["required_text"] = f"missing: {', '.join(missing_text)}"

    forbidden_actions = _as_list(verifier.get("forbidden_actions"))
    actions = _as_list(output.get("actions"))
    if forbidden_actions:
        actual_actions = set(actions)
        forbidden_seen = [item for item in forbidden_actions if item in actual_actions]
        scores["forbidden_actions"] = 0.0 if forbidden_seen else 1.0
        if forbidden_seen:
            errors["forbidden_actions"] = f"forbidden: {', '.join(forbidden_seen)}"

    required_artifacts = _as_list(verifier.get("required_artifacts"))
    if required_artifacts:
        if work_dir is not None:
            base = Path(work_dir)
            actual_artifacts = [
                item for item in required_artifacts if (base / item).exists()
            ]
        else:
            actual_artifacts = _as_list(output.get("artifacts"))
        _score_required(
            expected=required_artifacts,
            actual=actual_artifacts,
            key="required_artifacts",
            scores=scores,
            errors=errors,
        )

    return CaseVerificationResult(
        passed=all(score >= 1.0 for score in scores.values()),
        scores=scores,
        errors=errors,
    )

