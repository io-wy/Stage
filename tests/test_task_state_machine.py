"""Tests for TaskStateMachine — validated state transitions."""
from __future__ import annotations

import pytest
from openagents_orchestration.models.task import TaskNode, TaskStatus
from openagents_orchestration.core.task_state_machine import TaskStateMachine


class TestTaskStateMachine:
    def test_valid_transitions(self):
        """All valid transition paths for the coder↔reviewer workflow."""
        task = TaskNode("t1", "test", "coder")

        # PENDING → RUNNING
        r = TaskStateMachine.validate(task, TaskStatus.RUNNING)
        assert r.allowed

        # RUNNING → REVIEW
        task.status = TaskStatus.RUNNING
        r = TaskStateMachine.validate(task, TaskStatus.REVIEW)
        assert r.allowed

        # REVIEW → FIX_NEEDED
        task.status = TaskStatus.REVIEW
        r = TaskStateMachine.validate(task, TaskStatus.FIX_NEEDED)
        assert r.allowed

        # FIX_NEEDED → RUNNING
        task.status = TaskStatus.FIX_NEEDED
        r = TaskStateMachine.validate(task, TaskStatus.RUNNING)
        assert r.allowed

        # REVIEW → COMPLETED
        task.status = TaskStatus.REVIEW
        r = TaskStateMachine.validate(task, TaskStatus.COMPLETED)
        assert r.allowed

        # FAILED → PENDING (retry)
        task.status = TaskStatus.FAILED
        r = TaskStateMachine.validate(task, TaskStatus.PENDING)
        assert r.allowed

    def test_invalid_transitions(self):
        """Transitions that should be blocked."""
        task = TaskNode("t1", "test", "coder")

        # PENDING → COMPLETED (missing RUNNING)
        r = TaskStateMachine.validate(task, TaskStatus.COMPLETED)
        assert not r.allowed
        assert "pending" in r.detail and "completed" in r.detail

        # PENDING → FAILED (missing RUNNING)
        r = TaskStateMachine.validate(task, TaskStatus.FAILED)
        assert not r.allowed

        # COMPLETED → anything
        task.status = TaskStatus.COMPLETED
        r = TaskStateMachine.validate(task, TaskStatus.RUNNING)
        assert not r.allowed

        # SKIPPED → anything
        task.status = TaskStatus.SKIPPED
        r = TaskStateMachine.validate(task, TaskStatus.RUNNING)
        assert not r.allowed

    def test_allowed_transitions(self):
        """allowed_transitions returns human-readable list."""
        t = TaskStateMachine.allowed_transitions(TaskStatus.PENDING)
        assert "running" in t
        assert "skipped" in t
        assert "completed" not in t

    def test_is_terminal(self):
        assert not TaskStateMachine.is_terminal(TaskStatus.PENDING)
        assert not TaskStateMachine.is_terminal(TaskStatus.RUNNING)
        assert TaskStateMachine.is_terminal(TaskStatus.COMPLETED)
        assert not TaskStateMachine.is_terminal(TaskStatus.FAILED)  # can retry
        assert TaskStateMachine.is_terminal(TaskStatus.SKIPPED)

    def test_is_actionable(self):
        assert TaskStateMachine.is_actionable(TaskStatus.PENDING)
        assert TaskStateMachine.is_actionable(TaskStatus.RUNNING)
        assert TaskStateMachine.is_actionable(TaskStatus.REVIEW)
        assert TaskStateMachine.is_actionable(TaskStatus.FIX_NEEDED)
        assert not TaskStateMachine.is_actionable(TaskStatus.COMPLETED)
        assert not TaskStateMachine.is_actionable(TaskStatus.FAILED)

    def test_transition_applies_status(self):
        """TaskStateMachine.transition() actually changes the task."""
        task = TaskNode("t1", "test", "coder")
        r = TaskStateMachine.transition(task, TaskStatus.RUNNING, reason="spawned")
        assert r.allowed
        assert task.status == TaskStatus.RUNNING
        assert len(task.iteration_history) == 1
        assert "pending→running" in task.iteration_history[0]["action"]

    def test_transition_blocks_invalid(self):
        """TaskStateMachine.transition() does NOT change status on invalid."""
        task = TaskNode("t1", "test", "coder")
        r = TaskStateMachine.transition(task, TaskStatus.COMPLETED)
        assert not r.allowed
        assert task.status == TaskStatus.PENDING  # unchanged

    def test_running_can_fail(self):
        """RUNNING → FAILED is valid."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.RUNNING)
        r = TaskStateMachine.validate(task, TaskStatus.FAILED)
        assert r.allowed

    def test_fix_needed_can_fail(self):
        """FIX_NEEDED → FAILED (circuit breaker) is valid."""
        task = TaskNode("t1", "test", "coder", status=TaskStatus.FIX_NEEDED)
        r = TaskStateMachine.validate(task, TaskStatus.FAILED)
        assert r.allowed
