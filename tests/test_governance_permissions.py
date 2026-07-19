"""Tests for Stage governance permission gates."""

from __future__ import annotations

from openagents_orchestration.governance.permissions import PermissionEngine
from openagents_orchestration.governance.router import GovernanceRouter
from openagents_orchestration.intent_classifier import IntentFrame


def _frame(
    *,
    workflow_type: str = "service_case",
    business_process: str = "developer_platform_access_approval",
    risk_class: str = "privileged_action",
    human_handoff_policy: str = "ask_for_sponsor_and_scope",
) -> IntentFrame:
    return IntentFrame(
        task_type="unknown",
        complexity="medium",
        confidence=0.9,
        workflow_type=workflow_type,
        business_process=business_process,
        risk_class=risk_class,
        backend_plan=["rag_retrieval", "human_channel"],
        evidence_requirements=["source", "approval_owner", "scope"],
        closure_policy="verify_before_close",
        human_handoff_policy=human_handoff_policy,
        verifier_profile="service_desk",
    )


def test_permission_preflight_requires_human_for_privileged_action() -> None:
    frame = _frame()
    plan = GovernanceRouter().plan(frame)

    result = PermissionEngine().preflight(
        frame,
        plan,
        prompt="Please grant maintainer permission with admin access.",
    )

    assert result.passed is False
    assert result.blocked is False
    assert result.needs_human is True
    assert result.decisions[0].gate == "permission_preflight"
    assert result.decisions[0].capability == "privileged_action"
    assert result.decisions[0].decision == "needs_human"
    assert result.decisions[0].required_fields == ["approver", "target", "scope"]


def test_permission_preflight_allows_read_only_retrieval() -> None:
    frame = _frame(
        business_process="identity_and_access_support",
        risk_class="normal",
        human_handoff_policy="none",
    )
    plan = GovernanceRouter().plan(frame)

    result = PermissionEngine().preflight(
        frame,
        plan,
        prompt="Where can I read the documented NAS access path?",
    )

    assert result.passed is True
    assert result.needs_human is False
    assert result.decisions[0].capability == "read_or_analyze"
    assert result.decisions[0].decision == "allow"


def test_permission_preflight_allows_secret_trap_question_without_action() -> None:
    frame = _frame(risk_class="privileged_action", human_handoff_policy="none")
    plan = GovernanceRouter().plan(frame)

    result = PermissionEngine().preflight(
        frame,
        plan,
        prompt="Which directory should I use, and are admin API keys needed?",
    )

    assert result.passed is True
    assert result.needs_human is False
    assert result.decisions[0].capability == "read_or_analyze"


def test_permission_postcheck_blocks_unapproved_write_claim() -> None:
    frame = _frame()
    plan = GovernanceRouter().plan(frame)

    result = PermissionEngine().postcheck(
        frame,
        plan,
        output={
            "closed": True,
            "answer": "已授权 maintainer 权限，可以直接使用。",
            "actions": ["grant_maintainer_permission"],
        },
    )

    assert result.passed is False
    assert result.blocked is True
    assert result.decisions[0].gate == "permission_postcheck"
    assert result.decisions[0].capability == "claimed_write_action"
    assert result.decisions[0].decision == "block"


def test_permission_postcheck_allows_handoff_response_without_action_claim() -> None:
    frame = _frame()
    plan = GovernanceRouter().plan(frame)

    result = PermissionEngine().postcheck(
        frame,
        plan,
        output={
            "closed": False,
            "answer": "需要先确认审批人、目标项目和权限范围。",
            "actions": ["ask_human"],
            "human_questions": ["请提供审批人、目标项目和权限范围。"],
        },
    )

    assert result.passed is True
    assert result.blocked is False
    assert result.needs_human is False
