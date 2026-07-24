"""Tests for governance intent frames."""

from __future__ import annotations

from openagents_orchestration.intent_classifier import (
    IntentClassifier,
    IntentFrame,
    IntentResult,
)


def test_intent_frame_from_intent_result_preserves_core_fields() -> None:
    result = IntentResult(
        task_type="feature",
        complexity="medium",
        external=["github"],
        priority="normal",
        confidence=0.84,
        reason="keyword match",
        source="L1_rule",
    )

    frame = IntentFrame.from_intent_result(
        result,
        objective="implement a new API endpoint",
    )

    assert frame.task_type == "feature"
    assert frame.complexity == "medium"
    assert frame.external == ["github"]
    assert frame.priority == "normal"
    assert frame.confidence == 0.84
    assert frame.reason == "keyword match"
    assert frame.source == "L1_rule"
    assert frame.workflow_type == "development"
    assert frame.business_process == "code_task"
    assert frame.backend_plan == ["claude_code"]
    assert frame.closure_policy == "verify_before_close"


def test_classify_frame_stays_task_and_permission_first_for_service_requests() -> None:
    classifier = IntentClassifier(llm_client=None)

    frame = classifier.classify_frame(
        "A SAST member needs help resetting the NAS password"
    )

    assert frame.workflow_type == "general"
    assert frame.business_process == "unknown"
    assert frame.risk_class == "privileged_action"
    assert "rag_retrieval" in frame.backend_plan
    assert "claude_code" in frame.backend_plan
    assert "human_channel" not in frame.backend_plan
    assert frame.verifier_profile == "service_desk"


def test_classify_frame_does_not_treat_shared_account_mention_as_permission() -> None:
    classifier = IntentClassifier(llm_client=None)

    frame = classifier.classify_frame(
        "A member asks whether an old shared account can be used for testing"
    )

    assert frame.risk_class == "normal"


def test_classify_frame_serializes_to_dict() -> None:
    classifier = IntentClassifier(llm_client=None)

    frame = classifier.classify_frame("Please review this pull request")
    data = frame.to_dict()

    assert data["task_type"] == frame.task_type
    assert data["workflow_type"] == frame.workflow_type
    assert data["business_process"] == frame.business_process
    assert data["backend_plan"] == frame.backend_plan
    assert data["ambiguity_notes"] == frame.ambiguity_notes


def test_classify_frame_routes_chinese_python_function_request_to_code_task() -> None:
    classifier = IntentClassifier(llm_client=None)

    frame = classifier.classify_frame(
        "请帮我写一个 Python 函数 summarize_process_memory"
    )

    assert frame.workflow_type == "development"
    assert frame.business_process == "code_task"
    assert frame.backend_plan == ["claude_code"]
    assert frame.human_handoff_policy == "none"
