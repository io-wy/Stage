"""Tests for Stage governance record models."""

from __future__ import annotations

from openagents_orchestration.control.models import (
    ActionPlan,
    ActionResult,
    CaseAuditEvent,
    CaseRecord,
    CaseRunRecord,
    EvidenceEntry,
    HumanHandoffRecord,
    SafetyFinding,
    ToolInvocationRecord,
    VerificationFinding,
)


def test_action_models_capture_governed_execution_contract() -> None:
    plan = ActionPlan(
        case_id="case-1",
        run_id="run-1",
        action_type="grant_gitlab_role",
        executor="claude_code",
        adapter_id="claude_code_gh_repo_permission",
        adapter_tools=["claude_code", "gh-cli"],
        side_effect_level="privileged_write",
        allowed_actions=["prepare_change"],
        forbidden_actions=["grant_without_approval"],
        required_approval_fields=["approver", "target", "scope", "ticket_id"],
        required_evidence=["source", "approval_record"],
        verify_requirements=["confirm_project_member_role"],
        rollback_plan=["revoke_role"],
    )
    result = ActionResult(
        action_id=plan.action_id,
        executor=plan.executor,
        executed=False,
        actions_taken=[],
        errors=["missing ticket_id"],
    )

    assert plan.side_effect_level == "privileged_write"
    assert plan.adapter_id == "claude_code_gh_repo_permission"
    assert plan.adapter_tools == ["claude_code", "gh-cli"]
    assert "ticket_id" in plan.required_approval_fields
    assert result.action_id == plan.action_id
    assert result.executed is False
    assert result.errors == ["missing ticket_id"]


def test_governance_models_round_trip() -> None:
    case = CaseRecord(
        case_id="case-1",
        business_domain="service desk",
        business_process="identity_and_access_support",
        requester="member-1",
        request_text="reset NAS password",
        intent_frame={"workflow_type": "service_case"},
    )
    run = CaseRunRecord(
        run_id="run-1",
        case_id=case.case_id,
        backend_plan=["rag_retrieval", "claude_code"],
    )
    evidence = EvidenceEntry(
        evidence_id="e-1",
        case_id=case.case_id,
        run_id=run.run_id,
        source_ref="/docs/nas.md",
        retrieval_query="NAS password reset",
        summary="NAS reset path documented",
        used_by=["intent_router"],
    )
    tool = ToolInvocationRecord(
        invocation_id="t-1",
        case_id=case.case_id,
        run_id=run.run_id,
        backend="claude_code",
        output_ref="outputs/case_result.json",
    )
    safety = SafetyFinding(
        finding_id="s-1",
        case_id=case.case_id,
        run_id=run.run_id,
        surface="redactions.item",
        severity="high",
        finding_type="secret_leak",
        blocked=True,
    )
    verify = VerificationFinding(
        finding_id="v-1",
        case_id=case.case_id,
        run_id=run.run_id,
        gate="closure",
        passed=False,
        reason="missing human question",
    )
    handoff = HumanHandoffRecord(
        handoff_id="h-1",
        case_id=case.case_id,
        run_id=run.run_id,
        question="Did the member wait 2 minutes and retry?",
        owner="service-desk",
    )
    event = CaseAuditEvent(
        event_id="a-1",
        case_id=case.case_id,
        run_id=run.run_id,
        event_type="case_blocked",
        payload={"reason": "missing required information"},
    )

    assert case.business_process == "identity_and_access_support"
    assert run.backend_plan == ["rag_retrieval", "claude_code"]
    assert evidence.used_by == ["intent_router"]
    assert tool.backend == "claude_code"
    assert safety.blocked is True
    assert verify.passed is False
    assert handoff.status == "open"
    assert event.model_dump()["event_type"] == "case_blocked"
