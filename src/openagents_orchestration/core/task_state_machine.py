"""TaskStateMachine — explicit, validated state transitions for TaskNode.

This replaces ad-hoc ``update_task(task_id, status=...)`` calls that
previously allowed any state transition without validation.

Design principle: state transitions are a first-class domain concept,
not an implementation detail of StateBoard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openagents_orchestration.models.task import TaskStatus


# Allowed transitions: from → {to}
_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.SKIPPED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.REVIEW},
    TaskStatus.REVIEW: {TaskStatus.COMPLETED, TaskStatus.FIX_NEEDED, TaskStatus.FAILED},
    TaskStatus.FIX_NEEDED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    # Terminal states — no outgoing transitions (except for retry, see below)
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.PENDING},  # retry: reset to pending
    TaskStatus.SKIPPED: set(),
}


# Human-readable reasons required for each transition
_TRANSITION_REASONS: dict[tuple[TaskStatus, TaskStatus], str] = {
    (TaskStatus.PENDING, TaskStatus.RUNNING): "agent spawned",
    (TaskStatus.PENDING, TaskStatus.SKIPPED): "skipped by director",
    (TaskStatus.RUNNING, TaskStatus.COMPLETED): "agent completed successfully",
    (TaskStatus.RUNNING, TaskStatus.FAILED): "agent execution failed",
    (TaskStatus.RUNNING, TaskStatus.REVIEW): "coder signaled review ready",
    (TaskStatus.REVIEW, TaskStatus.COMPLETED): "reviewer approved",
    (TaskStatus.REVIEW, TaskStatus.FIX_NEEDED): "reviewer requested fixes",
    (TaskStatus.REVIEW, TaskStatus.FAILED): "circuit breaker or fatal review error",
    (TaskStatus.FIX_NEEDED, TaskStatus.RUNNING): "fix dispatched to coder",
    (TaskStatus.FIX_NEEDED, TaskStatus.FAILED): "circuit breaker exceeded max iterations",
    (TaskStatus.FAILED, TaskStatus.PENDING): "retry by director",
}


@dataclass
class TransitionResult:
    """Result of attempting a state transition."""

    allowed: bool
    from_status: TaskStatus
    to_status: TaskStatus
    reason: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.allowed


class TaskStateMachine:
    """Validates and records task state transitions.

    Usage::

        tsm = TaskStateMachine()
        result = tsm.transition(task, TaskStatus.RUNNING, reason="agent spawned")
        if not result.allowed:
            raise ValueError(f"Invalid transition: {result.detail}")
    """

    @staticmethod
    def validate(
        task: Any,  # TaskNode, duck-typed
        to_status: TaskStatus,
        *,
        reason: str = "",
    ) -> TransitionResult:
        """Check if a transition is valid. Does NOT mutate the task."""
        from_status = task.status if hasattr(task, "status") else TaskStatus.PENDING

        allowed_targets = _TRANSITIONS.get(from_status, set())
        if to_status not in allowed_targets:
            return TransitionResult(
                allowed=False,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                detail=f"Cannot transition from {from_status.value} → {to_status.value}. "
                       f"Allowed: {[s.value for s in allowed_targets]}",
            )

        return TransitionResult(
            allowed=True,
            from_status=from_status,
            to_status=to_status,
            reason=reason or _TRANSITION_REASONS.get((from_status, to_status), ""),
        )

    @staticmethod
    def transition(
        task: Any,
        to_status: TaskStatus,
        *,
        reason: str = "",
    ) -> TransitionResult:
        """Validate AND apply the transition. Returns the result."""
        result = TaskStateMachine.validate(task, to_status, reason=reason)
        if result.allowed:
            task.status = to_status
            # Record in iteration_history for audit trail
            if hasattr(task, "record_iteration"):
                task.record_iteration(
                    "state_machine",
                    f"transition:{result.from_status.value}→{result.to_status.value}",
                    f"reason={result.reason}",
                    [],
                )
        return result

    @staticmethod
    def allowed_transitions(status: TaskStatus) -> list[str]:
        """Return human-readable list of allowed next states."""
        return [s.value for s in _TRANSITIONS.get(status, set())]

    @staticmethod
    def is_terminal(status: TaskStatus) -> bool:
        """True if this status has no outgoing transitions (except retry)."""
        return len(_TRANSITIONS.get(status, set())) == 0

    @staticmethod
    def is_actionable(status: TaskStatus) -> bool:
        """True if a Director should keep working on tasks in this status."""
        return status in {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.REVIEW,
            TaskStatus.FIX_NEEDED,
        }
