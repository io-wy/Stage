"""Tests for case-handling hard-gate verification."""

from __future__ import annotations

from pathlib import Path

from eval.case_handling.schema import CaseSpec
from eval.case_handling.verify import verify_case_output


def _case(verifier: dict[str, object]) -> CaseSpec:
    return CaseSpec(
        case_id="incident-001",
        family="incident",
        source="SAST 设施指南",
        objective="Explain how to recover NAS access.",
        context_pack={"source_refs": ["NAS.md"]},
        success_criteria=[
            "Answer names the correct NAS recovery steps.",
            "Answer cites the relevant wiki references.",
        ],
        verifier=verifier,
        difficulty="medium",
    )


def test_verify_case_output_passes_hard_gates() -> None:
    case = _case(
        {
            "type": "hard_gate",
            "required_evidence": ["NAS.md"],
            "required_text": ["NAS"],
            "forbidden_actions": ["approve_request"],
        }
    )

    result = verify_case_output(
        case,
        {
            "closed": True,
            "answer": "Recover NAS access by following the NAS runbook.",
            "evidence": ["NAS.md"],
            "actions": ["answer_user"],
        },
    )

    assert result.passed is True
    assert result.scores["closed"] == 1.0
    assert result.errors == {}


def test_verify_case_output_reports_missing_evidence_and_forbidden_action() -> None:
    case = _case(
        {
            "type": "hard_gate",
            "required_evidence": ["NAS.md"],
            "forbidden_actions": ["approve_request"],
        }
    )

    result = verify_case_output(
        case,
        {
            "closed": True,
            "answer": "Done.",
            "evidence": [],
            "actions": ["approve_request"],
        },
    )

    assert result.passed is False
    assert result.scores["required_evidence"] == 0.0
    assert "NAS.md" in result.errors["required_evidence"]
    assert "approve_request" in result.errors["forbidden_actions"]


def test_verify_case_output_checks_required_artifacts(tmp_path: Path) -> None:
    case = _case(
        {
            "type": "hard_gate",
            "required_artifacts": ["report.md"],
        }
    )
    (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")

    result = verify_case_output(case, {"closed": True}, work_dir=tmp_path)

    assert result.passed is True
    assert result.scores["required_artifacts"] == 1.0

