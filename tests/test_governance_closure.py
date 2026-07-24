"""Tests for Stage governance closure gating."""

from __future__ import annotations

from types import SimpleNamespace

from openagents_orchestration.control.closure import evaluate_closure
from openagents_orchestration.control.safety import SafetyScanResult
from openagents_orchestration.intent_classifier import IntentClassifier, IntentFrame


def test_closure_gate_blocks_when_safety_or_verifier_fails() -> None:
    frame = IntentClassifier(llm_client=None).classify_frame(
        "A SAST member needs help resetting the NAS password"
    )
    safety = SafetyScanResult(blocked=True, findings=[])
    verifier = SimpleNamespace(passed=False, errors={"closed": "not verified"})
    output = {"closed": True, "human_questions": []}

    decision = evaluate_closure(frame, output, safety, verifier)

    assert decision.closed is False
    assert decision.reason == "safety_or_verification_failed"
    assert "safety" in decision.reasons
    assert "verification" in decision.reasons
    assert decision.needs_human is True


def test_closure_gate_allows_closure_when_gates_pass() -> None:
    frame = IntentClassifier(llm_client=None).classify_frame(
        "Please review this pull request"
    )
    safety = SafetyScanResult(blocked=False, findings=[])
    verifier = SimpleNamespace(passed=True, errors={})
    output = {"closed": True, "human_questions": []}

    decision = evaluate_closure(frame, output, safety, verifier)

    assert decision.closed is True
    assert decision.reason == "ok"
    assert decision.reasons == []
    assert decision.needs_human is False


def test_closure_gate_does_not_force_handoff_for_normal_ask_if_needed() -> None:
    frame = IntentFrame(
        task_type="feature",
        complexity="medium",
        confidence=0.85,
        workflow_type="development",
        business_process="code_task",
        risk_class="normal",
        backend_plan=["claude_code"],
        evidence_requirements=["source", "diff_or_patch", "verification"],
        closure_policy="verify_before_close",
        human_handoff_policy="ask_if_needed",
        verifier_profile="code_review",
        ambiguity_notes=["request contains ambiguity markers"],
    )
    safety = SafetyScanResult(blocked=False, findings=[])
    verifier = SimpleNamespace(passed=True, errors={})
    output = {"closed": True, "human_questions": []}

    decision = evaluate_closure(frame, output, safety, verifier)

    assert decision.closed is True
    assert decision.reason == "ok"
    assert decision.reasons == []
    assert decision.needs_human is False
