"""Tests for Stage governance comparison reporting."""

from __future__ import annotations

import json
from pathlib import Path

from eval.case_handling.governance_report import build_governance_comparison_report
from eval.case_handling.stage_governance_runner import run_stage_governance_benchmark
from tests.governance_fixtures import create_governance_replay_fixture


def test_governance_report_compares_against_baseline_summary(tmp_path: Path) -> None:
    evals_json, baseline_workspace, baseline_summary = create_governance_replay_fixture(
        tmp_path
    )

    stage_summary = run_stage_governance_benchmark(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "stage-governance",
    )
    baseline = json.loads(baseline_summary.read_text(encoding="utf-8"))

    report_path = tmp_path / "governance_report.json"
    report = build_governance_comparison_report(
        stage_summary.to_dict(),
        baseline,
        output_path=report_path,
    )

    assert report_path.exists()
    assert report["hard_gate_pass_rates"]["intent"] == 1.0
    assert report["hard_gate_pass_rates"]["domain"] == 1.0
    assert report["hard_gate_pass_rates"]["route"] == 1.0
    assert report["hard_gate_pass_rates"]["safety"] == 1.0
    assert report["hard_gate_pass_rates"]["closure"] == 1.0
    assert report["hard_gate_pass_rates"]["evidence"] == 1.0
    assert report["hard_gate_pass_rates"]["traceability"] == 1.0
    assert report["hard_gate_pass_rates"]["human_handoff"] == 1.0
    assert report["hard_gate_pass_rates"]["label_leakage_safe"] == 1.0
    assert report["audit_completeness"] == 1.0
