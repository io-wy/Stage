"""Tests for governance feedback capture and patch generation."""

from __future__ import annotations

from pathlib import Path

from openagents_orchestration.control.feedback import (
    CaseFeedbackRecord,
    build_regression_case,
    suggest_governance_patch,
    write_feedback_artifacts,
)


def _record() -> CaseFeedbackRecord:
    return CaseFeedbackRecord.from_governance(
        case_id="case-1",
        prompt="GitLab project sastoj needs Maintainer permission.",
        governance={
            "domain": {
                "pack_id": "sast_service_desk",
                "pack_version": "0.2.0",
                "business_process": "developer_platform_access_approval",
                "risk_class": "privileged_action",
                "backend_plan": ["rag_retrieval", "human_channel"],
                "evidence_requirements": ["source", "approval_owner", "scope"],
                "closure_policy": "verify_before_close",
                "human_handoff_policy": "ask_for_sponsor_and_scope",
                "verifier_profile": "service_desk",
                "permission_required_fields": ["approver", "scope"],
                "permission_action_markers": ["gitlab admin"],
            },
            "route": {"backends": ["rag_retrieval", "human_channel"]},
        },
        governed_case_result={
            "closed": True,
            "family": "ticket",
            "answer": "已授权",
            "actions": ["answer_user"],
        },
        labels=["wrong_closure", "should_require_approval", "should_not_use_claude_code"],
        note="This case should have required human approval.",
    )


def test_suggest_governance_patch_uses_feedback_labels() -> None:
    record = _record()

    patch = suggest_governance_patch(record)

    assert patch["pack_id"] == "sast_service_desk"
    rule = patch["rules"][0]
    assert rule["id"] == "feedback_case-1"
    assert "GitLab" in rule["keywords"][0]
    assert rule["profile"]["business_process"] == "developer_platform_access_approval"
    assert rule["profile"]["backend_plan"] == ["rag_retrieval", "human_channel"]
    assert "target" in rule["profile"]["permission_required_fields"]
    assert "ticket_id" in rule["profile"]["permission_required_fields"]


def test_suggest_governance_patch_extracts_explicit_safety_patterns() -> None:
    record = CaseFeedbackRecord.from_governance(
        case_id="case-secret",
        prompt="Do not expose internal token.",
        governance={
            "domain": {
                "business_process": "identity_and_access_support",
                "risk_class": "sensitive",
                "safety_forbidden_patterns": [],
            },
            "route": {"backends": ["rag_retrieval"]},
        },
        governed_case_result={"closed": False, "family": "ticket"},
        labels=["should_redact_secret", "forbid:internal-token"],
    )

    patch = suggest_governance_patch(record)
    regression_case = build_regression_case(record)

    assert patch["rules"][0]["profile"]["safety_forbidden_patterns"] == [
        "internal-token"
    ]
    assert any(
        assertion["name"] == "whole_output_secret_safe"
        for assertion in regression_case["assertions"]
    )


def test_build_regression_case_from_feedback_labels() -> None:
    record = _record()

    regression_case = build_regression_case(record)

    assert regression_case["expected_closed"] is False
    assert regression_case["business_process"] == "developer_platform_access_approval"
    assert any(
        assertion["name"] == "case_not_closed" for assertion in regression_case["assertions"]
    )
    assert any(
        assertion["name"] == "does_not_invoke_claude_code"
        for assertion in regression_case["assertions"]
    )


def test_write_feedback_artifacts_creates_docs_and_json(tmp_path: Path) -> None:
    record = _record()

    paths = write_feedback_artifacts(record, output_dir=tmp_path)

    assert Path(paths["feedback_json"]).exists()
    assert Path(paths["governance_patch"]).exists()
    assert Path(paths["regression_case"]).exists()
    assert Path(paths["markdown"]).exists()
