"""Tests for CollaborationStateMachine — coder↔reviewer protocol."""
from __future__ import annotations

import pytest
from openagents_orchestration.models.task import TaskNode, TaskStatus
from openagents_orchestration.core.collaboration import CollaborationSignal
from openagents_orchestration.core.collaboration_state_machine import (
    CollaborationAction,
    CollaborationStateMachine,
)


class TestCollaborationStateMachine:
    def test_review_ready_from_running(self):
        """Coder in RUNNING signals REVIEW_READY → REVIEW."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.RUNNING)
        task.assigned_agent = "coder-t1"
        csm = CollaborationStateMachine()
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.REVIEW_READY,
            from_id="coder-t1",
            content="tests passed = 3",
            tests_passed=3,
        )
        assert decision.action == CollaborationAction.TRANSITION_TO_REVIEW
        assert decision.tests_passed == 3

    def test_approved_from_review(self):
        """Reviewer signals APPROVED from REVIEW → COMPLETED."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.REVIEW)
        task.assigned_agent = "coder-t1"
        csm = CollaborationStateMachine()
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.APPROVED,
            from_id="reviewer-t1",
        )
        assert decision.action == CollaborationAction.TRANSITION_TO_COMPLETED
        assert decision.coder_id == "coder-t1"
        assert decision.reviewer_id == "reviewer-t1"

    def test_fix_needed_from_review(self):
        """Reviewer signals FIX_NEEDED from REVIEW → FIX_NEEDED."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.REVIEW)
        csm = CollaborationStateMachine()
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.FIX_NEEDED,
            from_id="reviewer-t1",
            content="line 42: missing null check",
        )
        assert decision.action == CollaborationAction.TRANSITION_TO_FIX_NEEDED
        assert decision.fix_content == "line 42: missing null check"

    def test_ignores_wrong_sender(self):
        """REVIEW_READY from unknown agent → NONE."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.RUNNING)
        task.assigned_agent = "coder-t1"
        csm = CollaborationStateMachine()
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.REVIEW_READY,
            from_id="someone-else",
        )
        assert decision.action == CollaborationAction.NONE

    def test_ignores_wrong_state(self):
        """APPROVED from PENDING → NONE (not in REVIEW)."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.PENDING)
        csm = CollaborationStateMachine()
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.APPROVED,
            from_id="reviewer-t1",
        )
        assert decision.action == CollaborationAction.NONE

    def test_circuit_breaker(self):
        """After max_iterations fix requests, triggers CIRCUIT_BREAK."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.REVIEW, max_iterations=2)
        # Simulate 2 previous fix iterations
        task.iteration_history = [
            {"action": "reviewer_requested_fix", "agent_id": "reviewer-t1"},
            {"action": "reviewer_requested_fix", "agent_id": "reviewer-t1"},
        ]
        csm = CollaborationStateMachine(max_iterations=2)
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.FIX_NEEDED,
            from_id="reviewer-t1",
        )
        assert decision.action == CollaborationAction.CIRCUIT_BREAK
        assert "2" in decision.reason

    def test_circuit_breaker_not_triggered_below_limit(self):
        """Below max_iterations, FIX_NEEDED still works."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.REVIEW, max_iterations=5)
        task.iteration_history = [
            {"action": "reviewer_requested_fix", "agent_id": "reviewer-t1"},
        ]
        csm = CollaborationStateMachine()
        decision = csm.decide(
            task=task,
            signal=CollaborationSignal.FIX_NEEDED,
            from_id="reviewer-t1",
        )
        assert decision.action == CollaborationAction.TRANSITION_TO_FIX_NEEDED

    def test_uses_task_max_iterations(self):
        """Respects task.max_iterations over csm default."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.REVIEW, max_iterations=3)
        task.iteration_history = [
            {"action": "reviewer_requested_fix", "agent_id": "r1"},
            {"action": "reviewer_requested_fix", "agent_id": "r1"},
        ]
        csm = CollaborationStateMachine(max_iterations=100)  # generous default
        decision = csm.decide(task=task, signal=CollaborationSignal.APPROVED, from_id="reviewer-t1")
        assert decision.action == CollaborationAction.TRANSITION_TO_COMPLETED  # below task's 3
