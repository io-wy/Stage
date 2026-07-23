"""Tests for the Stage governance service-demo report."""

from __future__ import annotations

from pathlib import Path

from eval.case_handling.stage_governance_demo import build_stage_governance_demo
from tests.governance_fixtures import create_governance_replay_fixture


def test_stage_governance_demo_writes_human_readable_artifacts(tmp_path: Path) -> None:
    evals_json, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    report = build_stage_governance_demo(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "demo",
    )

    assert report["demo_case_count"] == 6
    assert Path(report["json_path"]).exists()
    assert Path(report["markdown_path"]).exists()
    assert Path(report["html_path"]).exists()

    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert "Stage 服务治理演示" in markdown
    assert "不是完整 DAG 执行器" in markdown
    assert "权限判断" in markdown
    assert "Baseline 差异" in markdown
    html = Path(report["html_path"]).read_text(encoding="utf-8")
    assert "Stage 服务治理演示" in html
    assert "打开过程文件" in html
    assert "线上反馈回流" in html
    assert "下载反馈包" in html
    assert "Replay 发布判断" in html


def test_stage_governance_demo_highlights_governance_decisions(tmp_path: Path) -> None:
    evals_json, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    report = build_stage_governance_demo(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "demo",
    )
    by_id = {case["eval_id"]: case for case in report["cases"]}

    gitlab = by_id[3]
    assert gitlab["permission"]["permission_level"] == "privileged_action"
    assert gitlab["permission"]["decision"] == "blocked_until_human_input"
    assert "approval_owner" in gitlab["permission"]["required_human_fields"]
    assert "human_channel" in gitlab["route"]["backends"]

    sastoj = by_id[6]
    assert "subagent" in [node["capability"] for node in sastoj["route"]["nodes"]]
    assert sastoj["permission"]["decision"] == "blocked_until_human_input"

    nas_knowledge = by_id[12]
    assert nas_knowledge["stage_decision"]["closed"] is True
    assert nas_knowledge["permission"]["decision"] == "allowed_to_close"


def test_stage_governance_demo_includes_user_feedback_templates(
    tmp_path: Path,
) -> None:
    evals_json, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    report = build_stage_governance_demo(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "demo",
    )

    assert "feedback_workflow" in report
    assert "labels" in report["feedback_workflow"]
    assert "wrong_closure" in {
        label["id"] for label in report["feedback_workflow"]["labels"]
    }

    case = report["cases"][0]
    assert case["feedback_template"]["case_id"] == "hard-v2-0"
    assert case["feedback_template"]["governance_ref"].endswith("governance.json")
    assert case["feedback_template"]["case_result_ref"].endswith("case_result.json")
    assert case["feedback_template"]["observed"]["closed"] is False
    assert "stage_decision" in case["feedback_template"]["observed"]


def test_stage_governance_demo_compares_against_baseline_overclosure(
    tmp_path: Path,
) -> None:
    evals_json, baseline_workspace, _ = create_governance_replay_fixture(tmp_path)

    report = build_stage_governance_demo(
        evals_json=evals_json,
        baseline_workspace=baseline_workspace,
        output_root=tmp_path / "demo",
    )
    by_id = {case["eval_id"]: case for case in report["cases"]}

    nas_registration = by_id[0]
    assert nas_registration["stage_decision"]["closed"] is False
    assert nas_registration["baseline_decision"]["closed"] is True
    assert "baseline_over_closed" in nas_registration["baseline_gaps"]
    assert "stage_structured_handoff" in nas_registration["baseline_gaps"]
