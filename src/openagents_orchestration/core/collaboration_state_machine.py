"""CollaborationStateMachine — explicit state machine for producer↔checker loops.

Previously this logic was embedded as if/elif chains in
OrchestratorRunner._apply_collaboration_signal(). Extracting it makes
the collaboration protocol testable, documentable, and independently
evolvable.

Protocol:
  RUNNING  --[REVIEW_READY]--> REVIEW  --[APPROVED]--> COMPLETED
  REVIEW   --[FIX_NEEDED]---> FIX_NEEDED --[fix sent]--> RUNNING
  FIX_NEEDED (too many iterations) --> FAILED (circuit breaker)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CollaborationAction(StrEnum):
    """Actions the collaboration loop should take after processing a signal."""

    NONE = "none"                    # No action needed
    TRANSITION_TO_REVIEW = "review"  # Mark task REVIEW
    TRANSITION_TO_COMPLETED = "completed"  # Mark task COMPLETED, stop agents
    TRANSITION_TO_FIX_NEEDED = "fix_needed"  # Mark task FIX_NEEDED, sleep checker
    CIRCUIT_BREAK = "circuit_break"  # Too many iterations, mark FAILED
    SLEEP_REVIEWER = "sleep_reviewer"  # Backward-compatible unused action


@dataclass(frozen=True)
class CollaborationDecision:
    """What the collaboration loop should do next for a task."""

    action: CollaborationAction
    task_id: str
    reason: str = ""
    producer_id: str = ""  # resident_id of producer to stop/send to
    checker_id: str = ""   # resident_id of checker to stop/sleep
    fix_content: str = ""  # checker's feedback for the producer
    tests_passed: int = 0  # from the REVIEW_READY signal

    @property
    def coder_id(self) -> str:
        """Backward-compatible alias for legacy tests/callers."""
        return self.producer_id

    @property
    def reviewer_id(self) -> str:
        """Backward-compatible alias for legacy tests/callers."""
        return self.checker_id


class CollaborationStateMachine:
    """State machine for a single producer↔checker task cycle.

    Pure logic — no side effects. Returns CollaborationDecision;
    the caller (OrchestratorRunner) executes the decision.
    """

    def __init__(self, max_iterations: int = 5, role_map: dict[str, str] | None = None):
        self._max_iterations = max_iterations
        self._role_map = role_map or {}

    def decide(
        self,
        task: Any,  # TaskNode, duck-typed
        signal: Any,  # CollaborationSignal Enum value
        from_id: str,
        content: str = "",
        tests_passed: int = 0,
    ) -> CollaborationDecision:
        """Given current task state + incoming signal, decide what to do."""
        from openagents_orchestration.core.collaboration import CollaborationSignal
        from openagents_orchestration.models.task import TaskStatus

        task_id = task.task_id if hasattr(task, "task_id") else ""
        status = task.status if hasattr(task, "status") else TaskStatus.PENDING
        producer_id = self._role_map.get(
            "producer",
            task.assigned_agent if hasattr(task, "assigned_agent") else "",
        )
        checker_id = self._role_map.get("checker", f"reviewer-{task_id}")

        history = getattr(task, "iteration_history", []) or []
        fix_iterations = sum(
            1 for entry in history
            if entry.get("action") in {"checker_requested_fix", "reviewer_requested_fix"}
        )
        max_iter = getattr(task, "max_iterations", self._max_iterations) or self._max_iterations

        if fix_iterations >= max_iter:
            return CollaborationDecision(
                action=CollaborationAction.CIRCUIT_BREAK,
                task_id=task_id,
                reason=f"{fix_iterations} fix iterations (max={max_iter})",
                producer_id=producer_id,
                checker_id=checker_id,
            )

        if status == TaskStatus.RUNNING and from_id == producer_id and signal == CollaborationSignal.REVIEW_READY:
            return CollaborationDecision(
                action=CollaborationAction.TRANSITION_TO_REVIEW,
                task_id=task_id,
                reason="producer signaled review ready",
                producer_id=producer_id,
                checker_id=checker_id,
                tests_passed=tests_passed,
            )

        if status == TaskStatus.REVIEW and from_id == checker_id and signal == CollaborationSignal.APPROVED:
            return CollaborationDecision(
                action=CollaborationAction.TRANSITION_TO_COMPLETED,
                task_id=task_id,
                reason="checker approved",
                producer_id=producer_id,
                checker_id=checker_id,
            )

        if status == TaskStatus.REVIEW and from_id == checker_id and signal == CollaborationSignal.FIX_NEEDED:
            return CollaborationDecision(
                action=CollaborationAction.TRANSITION_TO_FIX_NEEDED,
                task_id=task_id,
                reason="checker requested fixes",
                producer_id=producer_id,
                checker_id=checker_id,
                fix_content=content,
            )

        return CollaborationDecision(action=CollaborationAction.NONE, task_id=task_id)
