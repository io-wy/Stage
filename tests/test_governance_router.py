"""Tests for Stage governance backend planning."""

from __future__ import annotations

from openagents_orchestration.governance.domain import (
    GovernanceDomainProfile,
    GovernanceDomainResolver,
    apply_domain_profile,
)
from openagents_orchestration.governance.router import GovernanceRouter
from openagents_orchestration.intent_classifier import IntentClassifier


def test_router_plans_service_desk_backends() -> None:
    prompt = "A SAST member needs help resetting the NAS password"
    frame = IntentClassifier(llm_client=None).classify_frame(prompt)
    frame = apply_domain_profile(frame, GovernanceDomainResolver().resolve(prompt))
    plan = GovernanceRouter().plan(frame)

    assert plan.route_label == "service_desk"
    assert plan.backends[0] == "rag_retrieval"
    assert plan.backends == ["rag_retrieval", "human_channel"]
    assert plan.needs_human is True
    assert plan.business_process == "identity_and_access_support"
    assert [node.capability for node in plan.nodes[:3]] == [
        "rag_retrieval",
        "human_channel",
        "verify",
    ]


def test_router_keeps_development_backends_simple() -> None:
    frame = IntentClassifier(llm_client=None).classify_frame(
        "Please review this pull request"
    )
    plan = GovernanceRouter().plan(frame)

    assert plan.route_label == "development"
    assert plan.backends == ["claude_code"]
    assert plan.needs_human is False
    assert [node.capability for node in plan.nodes[:2]] == ["claude_code", "verify"]


def test_router_respects_domain_backend_plan_without_forcing_claude_code() -> None:
    frame = IntentClassifier(llm_client=None).classify_frame(
        "How do I access NAS from the public network?"
    )
    frame = apply_domain_profile(
        frame,
        GovernanceDomainProfile(
            workflow_type="service_case",
            business_process="identity_and_access_support",
            risk_class="normal",
            backend_plan=["rag_retrieval"],
            closure_policy="standard",
            human_handoff_policy="none",
        ),
    )

    plan = GovernanceRouter().plan(frame)

    assert plan.route_label == "service_desk"
    assert plan.backends == ["rag_retrieval"]
    assert plan.needs_human is False
    assert [node.capability for node in plan.nodes[:2]] == ["rag_retrieval", "verify"]


def test_router_combines_domain_plan_with_risk_and_evidence_requirements() -> None:
    frame = IntentClassifier(llm_client=None).classify_frame(
        "Investigate a production database error"
    )
    frame = apply_domain_profile(
        frame,
        GovernanceDomainProfile(
            workflow_type="service_case",
            business_process="developer_service_defect_triage",
            risk_class="sensitive",
            backend_plan=["rag_retrieval"],
            evidence_requirements=["source", "logs", "reproduction"],
            closure_policy="verify_before_close",
            human_handoff_policy="ask_for_logs_and_reproduction",
        ),
    )

    plan = GovernanceRouter().plan(frame)

    assert plan.backends == ["rag_retrieval", "subagent", "human_channel"]
    assert plan.needs_human is True
    assert [node.capability for node in plan.nodes[:4]] == [
        "rag_retrieval",
        "subagent",
        "human_channel",
        "verify",
    ]
