"""Tests for the Stage governance eval runner."""

from __future__ import annotations

from pathlib import Path

from eval.case_handling.stage_governance_runner import (
    HardV2EvalSpec,
    run_stage_governance_benchmark,
    run_stage_governance_eval,
)
from tests.governance_fixtures import create_governance_replay_fixture


def test_stage_governance_runner_processes_hard_v2(tmp_path: Path) -> None:
    evals_json, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    summary = run_stage_governance_benchmark(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "stage-governance",
    )

    assert summary.total_evals == 6
    assert summary.closed_correct >= 5
    assert summary.family_correct >= 5
    assert summary.secret_safe == 6
    assert summary.intent_correct == 6
    assert summary.domain_correct == 6
    assert summary.route_correct == 6
    assert summary.traceability_correct == 6
    assert summary.audit_complete == 6
    assert summary.label_leakage_safe == 6
    assert summary.overall_pass_rate > 0.75
    assert summary.report_path is not None
    assert Path(summary.report_path).exists()

    by_id = {item.eval_id: item for item in summary.evals}

    nas = by_id[0]
    assert nas.governed_case_result["closed"] is False
    assert nas.governed_case_result["family"] == "ticket"
    assert nas.governed_case_result["human_questions"]
    assert "correct_closure" not in nas.governed_case_result["hard_gates"]
    assert nas.governance["domain"]["business_process"] == "identity_and_access_support"
    assert Path(nas.audit_path).exists()
    assert Path(nas.governance_path).exists()
    assert nas.governance["claim_trace"]

    overleaf = by_id[7]
    assert overleaf.governed_case_result["family"] == "approval"
    assert overleaf.governance["domain"]["business_process"] == (
        "collaboration_platform_change_approval"
    )

    nas_knowledge = by_id[12]
    assert nas_knowledge.governed_case_result["family"] == "knowledge"
    assert nas_knowledge.governed_case_result["closed"] is True


def test_stage_governance_runner_writes_audit_events(tmp_path: Path) -> None:
    evals_json, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    summary = run_stage_governance_benchmark(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "stage-governance",
        eval_ids=[0],
    )

    item = summary.evals[0]
    audit_text = Path(item.audit_path).read_text(encoding="utf-8")

    assert summary.total_evals == 1
    assert "intent_classified" in audit_text
    assert "backend_planned" in audit_text
    assert "closure_checked" in audit_text
    assert "case_blocked" in audit_text or "case_closed" in audit_text


def test_stage_governance_eval_does_not_copy_expected_labels(tmp_path: Path) -> None:
    _, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    spec = HardV2EvalSpec(
        eval_id=0,
        eval_name="nas-registration-notification-safe-closure",
        family="approval",
        business_process="developer_platform_access_approval",
        expected_closed=True,
        prompt="A SAST member needs help resetting the NAS password",
        assertions=[],
        files=[],
    )

    result = run_stage_governance_eval(
        spec,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "stage-governance",
    )

    assert result.governed_case_result["family"] == "ticket"
    assert result.governed_case_result["closed"] is False
    assert result.governance["domain"]["business_process"] == (
        "identity_and_access_support"
    )
    assert "expected_closed" not in result.governed_case_result["hard_gates"]
    assert "correct_closure" not in result.governed_case_result["hard_gates"]
    assert result.grade["family"] == "approval"
    assert result.grade["actual_family"] == "ticket"
