"""Collaboration message parsing + TaskNode serialization coverage.

The producer/checker orchestration loop (``_process_collaborative_messages`` /
``_run_collaborative``) was extracted out of ``OrchestratorRunner`` into
``CollaborationStateMachine`` / ``CollaborationDecisionExecutor``; that logic
is covered by ``test_collaboration_state_machine.py`` and
``test_collaboration_executor.py``.  This file keeps only the pure-function and
serialization tests that do not depend on the runner methods.
"""

from __future__ import annotations

from openagents_orchestration.core.collaboration import (
    CollaborationSignal,
    parse_collaboration_message,
    task_id_from_resident_id,
)
from openagents_orchestration.models.task import TaskNode


def test_parse_collaboration_message_preserves_task_and_test_count():
    parsed = parse_collaboration_message(
        "TASK_REVIEW_READY[api-auth]: tests passed = 12",
        default_task_id="fallback",
    )

    assert parsed is not None
    assert parsed.signal == CollaborationSignal.REVIEW_READY
    assert parsed.task_id == "api-auth"
    assert parsed.tests_passed == 12
    assert task_id_from_resident_id("reviewer-api-auth") == "api-auth"


def test_task_node_serializes_collaboration_fields():
    task = TaskNode(
        "research-api",
        "research API options",
        "researcher",
        collaboration_pattern="producer_checker",
        collaboration_participants={"producer": "researcher", "checker": "reviewer"},
    )

    restored = TaskNode.from_dict(task.to_dict())

    assert restored.collaboration_pattern == "producer_checker"
    assert restored.collaboration_participants == {
        "producer": "researcher",
        "checker": "reviewer",
    }


def test_task_node_loads_legacy_dict_without_collaboration_fields():
    task = TaskNode.from_dict({
        "task_id": "legacy",
        "description": "legacy task",
        "agent_type": "coder",
    })

    assert task.collaboration_pattern == "default"
    assert task.collaboration_participants == {}


def test_task_id_from_resident_id_accepts_any_agent_type():
    assert task_id_from_resident_id("researcher-api-auth") == "api-auth"
    assert task_id_from_resident_id("github_agent-create-pr") == "create-pr"
    assert task_id_from_resident_id("monitor-release-check") == "release-check"
