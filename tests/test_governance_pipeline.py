"""Tests for the Stage governance execution pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from openagents_orchestration.governance.domain import GovernanceDomainResolver
from openagents_orchestration.governance.pipeline import (
    ReplayCaseBackend,
    StageGovernancePipeline,
)
from openagents_orchestration.intent_classifier import IntentClassifier, IntentSchema


def test_stage_governance_pipeline_executes_backend_and_governs_output(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "ticket",
            "answer": "Wait two minutes and retry NAS registration.",
            "evidence": [
                {
                    "file": "/docs/nas.md",
                    "summary": "NAS registration retry path is documented.",
                }
            ],
            "human_questions": [],
            "actions": ["answer_user"],
            "failure_mode": None,
        },
        execution_mode="test_replay",
    )

    result = StageGovernancePipeline().run(
        prompt="A SAST member needs help resetting the NAS password",
        case_id="case-1",
        run_id="run-1",
        backend=backend,
        audit_path=tmp_path / "audit.jsonl",
    )

    assert backend.calls == 1
    assert result.governed_case_result["closed"] is False
    assert result.governed_case_result["family"] == "ticket"
    assert result.governed_case_result["human_questions"]
    assert result.governance_payload["execution_mode"] == "test_replay"
    assert result.governance_payload["domain"]["business_process"] == (
        "identity_and_access_support"
    )
    assert result.governance_payload["route"]["route_label"] == "service_desk"

    audit_events = [
        json.loads(line)["event_type"]
        for line in Path(result.audit_path).read_text(encoding="utf-8").splitlines()
    ]
    assert "tool_invoked" in audit_events
    assert "domain_resolved" in audit_events
    assert "case_blocked" in audit_events


def test_stage_governance_pipeline_uses_routing_prompt_for_intent(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "knowledge",
            "answer": "Use documented NAS access paths.",
            "evidence": [{"file": "/docs/nas.md", "summary": "NAS access paths."}],
            "human_questions": [],
            "actions": ["answer_user"],
            "failure_mode": None,
        },
    )

    result = StageGovernancePipeline().run(
        prompt="Produce JSON and include API testing details.",
        routing_prompt="A member asks for complete safe ways to access NAS.",
        case_id="case-2",
        run_id="run-2",
        backend=backend,
        audit_path=tmp_path / "audit-routing.jsonl",
    )

    assert result.intent_frame.task_type == "unknown"
    assert result.route_plan.backends == ["rag_retrieval"]
    assert result.governance_payload["routing_prompt"] == (
        "A member asks for complete safe ways to access NAS."
    )


def test_stage_governance_pipeline_uses_llm_intent_then_domain_governance(
    tmp_path: Path,
) -> None:
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(
        return_value=MagicMock(
            output_text=IntentSchema(
                task_type="shell",
                complexity="medium",
                external=["sast-link"],
                priority="normal",
                confidence=0.86,
                reason="用户请求执行一项服务操作。",
            ).model_dump_json(),
            usage=MagicMock(total_tokens=10),
        )
    )
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "approval",
            "answer": "已开通 sast-link 权限。",
            "evidence": [
                {
                    "file": "/docs/sast-link.md",
                    "summary": "sast-link 权限申请需要审批人、目标、范围和工单号。",
                }
            ],
            "human_questions": [],
            "actions": ["grant_sast_link_permission"],
            "failure_mode": None,
        },
        execution_mode="test_replay",
    )

    result = StageGovernancePipeline(
        intent_classifier=IntentClassifier(llm_client=mock_llm)
    ).run(
        prompt="给我开个 sast-link 的权限，先开一下，审批人后面补。",
        case_id="case-sast-link",
        run_id="run-sast-link",
        backend=backend,
        audit_path=tmp_path / "audit-sast-link.jsonl",
    )

    assert result.intent_frame.source == "L3_llm"
    assert result.intent_frame.reason == "用户请求执行一项服务操作。"
    assert result.governance_frame.business_process == "sast_link_access_approval"
    assert result.governance_frame.risk_class == "privileged_action"
    assert result.route_plan.backends == ["rag_retrieval", "human_channel"]
    assert result.governed_case_result["closed"] is False
    assert result.governed_case_result["failure_mode"] == "permission_policy_failure"
    assert result.governance_payload["action_plan"]["required_approval_fields"] == [
        "approver",
        "target",
        "scope",
        "ticket_id",
    ]
    mock_llm.generate.assert_awaited_once()


def test_stage_governance_pipeline_blocks_unapproved_privileged_closure(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "ticket",
            "answer": "已授权 maintainer 权限，可以直接使用。",
            "evidence": [
                {
                    "file": "/docs/gitlab.md",
                    "summary": "GitLab permission changes require sponsor and scope.",
                }
            ],
            "human_questions": [],
            "actions": ["grant_maintainer_permission"],
            "failure_mode": None,
        },
        execution_mode="test_replay",
    )

    result = StageGovernancePipeline().run(
        prompt=(
            "GitLab project sastoj needs Maintainer permission. "
            "Sponsor is offline; use admin access directly."
        ),
        case_id="case-gitlab",
        run_id="run-permission",
        backend=backend,
        audit_path=tmp_path / "audit-permission.jsonl",
    )

    assert backend.calls == 1
    assert result.governed_case_result["closed"] is False
    assert result.governed_case_result["failure_mode"] == "permission_policy_failure"
    assert result.governed_case_result["hard_gates"]["permission_passed"] is False
    assert result.governance_payload["action_plan"]["executor"] == "human"
    assert result.governance_payload["action_plan"]["side_effect_level"] == (
        "privileged_write"
    )
    assert "external_write_without_approval" in (
        result.governance_payload["action_plan"]["forbidden_actions"]
    )
    assert result.governance_payload["action_result"]["executed"] is False
    assert result.governance_payload["permissions"]["preflight"]["needs_human"] is True
    assert result.governance_payload["permissions"]["postcheck"]["blocked"] is True
    assert result.governance_payload["domain"]["business_process"] == (
        "developer_platform_access_approval"
    )
    assert result.governed_case_result["human_questions"]

    audit_events = [
        json.loads(line)["event_type"]
        for line in Path(result.audit_path).read_text(encoding="utf-8").splitlines()
    ]
    assert "permission_preflight_checked" in audit_events
    assert "action_planned" in audit_events
    assert "action_result_recorded" in audit_events
    assert "permission_postcheck_checked" in audit_events
    assert "case_blocked" in audit_events


def test_stage_governance_pipeline_exposes_governed_adapter_contract(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": False,
            "family": "approval",
            "answer": "缺少审批人和工单号，已转人工确认。",
            "evidence": [
                {
                    "file": "/docs/github.md",
                    "summary": "GitHub repository permission changes require approval.",
                }
            ],
            "human_questions": ["请补充审批人、目标用户、权限范围和工单号。"],
            "actions": ["create_handoff"],
            "failure_mode": "permission_policy_failure",
        },
        execution_mode="test_replay",
    )

    result = StageGovernancePipeline().run(
        prompt="给 alice 开 GitHub repo admin 权限，先帮我操作，工单后面补。",
        case_id="case-github",
        run_id="run-github",
        backend=backend,
        audit_path=tmp_path / "audit-github.jsonl",
    )

    action_plan = result.governance_payload["action_plan"]
    assert result.governance_payload["domain"]["business_process"] == (
        "github_repo_access_approval"
    )
    assert action_plan["executor"] == "human"
    assert action_plan["adapter_id"] == "claude_code_gh_repo_permission"
    assert action_plan["adapter_tools"] == ["claude_code", "gh-cli", "gh-skill"]
    assert action_plan["side_effect_level"] == "privileged_write"
    assert "grant_without_approval" in action_plan["forbidden_actions"]
    assert result.governance_payload["action_result"]["executed"] is False


def test_stage_governance_pipeline_emits_source_to_claim_trace(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "knowledge",
            "answer": "Use documented NAS access paths.",
            "evidence": [{"file": "/docs/nas.md", "summary": "NAS access paths."}],
            "human_questions": [],
            "actions": ["answer_user"],
            "failure_mode": None,
        },
    )

    result = StageGovernancePipeline().run(
        prompt="A member asks for complete safe ways to access NAS.",
        case_id="case-trace",
        run_id="run-trace",
        backend=backend,
        audit_path=tmp_path / "audit-trace.jsonl",
    )

    trace = result.governance_payload["claim_trace"]
    assert trace
    assert result.governed_case_result["claim_trace"] == trace
    assert result.governed_case_result["hard_gates"]["traceability_passed"] is True
    assert result.governance_payload["public_evidence"][0]["evidence_id"]
    assert result.governance_payload["public_evidence"][0]["supports_claims"]


def test_stage_governance_pipeline_preserves_backend_action_result(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "knowledge",
            "answer": "Use documented NAS access paths.",
            "evidence": [{"file": "/docs/nas.md", "summary": "NAS access paths."}],
            "actions": ["answer_user"],
            "human_questions": [],
            "failure_mode": None,
            "action_result": {
                "executor": "mock_executor",
                "executed": True,
                "actions_taken": ["answer_user"],
                "external_refs": {"ticket": "T-1"},
                "verification_claims": ["answer backed by NAS source"],
            },
        },
    )

    result = StageGovernancePipeline().run(
        prompt="A member asks for complete safe ways to access NAS.",
        case_id="case-action-result",
        run_id="run-action-result",
        backend=backend,
        audit_path=tmp_path / "audit-action-result.jsonl",
    )

    action_result = result.governance_payload["action_result"]
    assert action_result["executed"] is True
    assert action_result["executor"] == "mock_executor"
    assert action_result["external_refs"] == {"ticket": "T-1"}
    assert action_result["verification_claims"] == ["answer backed by NAS source"]


def test_stage_governance_pipeline_blocks_closure_with_irrelevant_evidence(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "knowledge",
            "answer": "Use the documented NAS access paths.",
            "evidence": [
                {
                    "file": "/docs/sastoj.md",
                    "summary": "SASTOJ database table schema and SQL indexes.",
                }
            ],
            "human_questions": [],
            "actions": ["answer_user"],
            "failure_mode": None,
        },
    )

    result = StageGovernancePipeline().run(
        prompt="A member asks for complete safe ways to access NAS.",
        case_id="case-irrelevant-evidence",
        run_id="run-irrelevant-evidence",
        backend=backend,
        audit_path=tmp_path / "audit-irrelevant-evidence.jsonl",
    )

    public_evidence = result.governance_payload["public_evidence"]
    assert result.governed_case_result["closed"] is False
    assert result.governed_case_result["failure_mode"] == "verification_failure"
    assert result.governed_case_result["hard_gates"]["verification_passed"] is False
    assert result.governance_payload["verification"]["passed"] is False
    assert result.governance_payload["verification"]["reasons"] == [
        "no_relevant_public_evidence"
    ]
    assert public_evidence[0]["selected"] is False
    assert public_evidence[0]["relevance"]["passed"] is False


def test_stage_governance_pipeline_does_not_select_restricted_evidence(
    tmp_path: Path,
) -> None:
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "knowledge",
            "answer": "Use documented NAS access paths.",
            "evidence": [
                    {
                        "file": "/docs/nas-sensitive.md",
                        "summary": "NAS restricted recovery details.",
                        "tags": ["wiki", "perm:sensitive"],
                    }
            ],
            "human_questions": [],
            "actions": ["answer_user"],
            "failure_mode": None,
        },
    )

    result = StageGovernancePipeline().run(
        prompt="A member asks for complete safe ways to access NAS.",
        case_id="case-sensitive-evidence",
        run_id="run-sensitive-evidence",
        backend=backend,
        audit_path=tmp_path / "audit-sensitive-evidence.jsonl",
    )

    public_evidence = result.governance_payload["public_evidence"]
    assert result.governed_case_result["closed"] is False
    assert result.governed_case_result["failure_mode"] == "verification_failure"
    assert public_evidence[0]["sensitivity"] == "restricted"
    assert public_evidence[0]["summary"] == "[redacted]"
    assert public_evidence[0]["selected"] is False


def test_stage_governance_pipeline_uses_domain_pack_safety_patterns(
    tmp_path: Path,
) -> None:
    pack_path = tmp_path / "domain_pack.yaml"
    pack_path.write_text(
        """
rules:
  - id: lab_network
    keywords: ["lab vpn"]
    profile:
      workflow_type: service_case
      business_process: lab_network_access
      risk_class: normal
      backend_plan: ["rag_retrieval"]
      evidence_requirements: ["source"]
      closure_policy: standard
      human_handoff_policy: none
      verifier_profile: service_desk
      safety_forbidden_patterns: ["internal-only route"]
""",
        encoding="utf-8",
    )
    backend = ReplayCaseBackend(
        {
            "closed": True,
            "family": "knowledge",
            "answer": "Use the internal-only route.",
            "evidence": [{"file": "/docs/vpn.md", "summary": "VPN access guide."}],
            "human_questions": [],
            "actions": ["answer_user"],
            "failure_mode": None,
        },
        execution_mode="test_replay",
    )

    result = StageGovernancePipeline(
        domain_resolver=GovernanceDomainResolver(domain_pack_paths=[pack_path])
    ).run(
        prompt="How do I use the lab vpn?",
        case_id="case-safety-pack",
        run_id="run-safety-pack",
        backend=backend,
        audit_path=tmp_path / "audit-safety-pack.jsonl",
    )

    assert result.governed_case_result["closed"] is False
    assert result.governed_case_result["failure_mode"] == "safety_policy_failure"
    assert result.governance_payload["safety"]["blocked"] is True
