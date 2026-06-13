"""CollaborationDecisionExecutor — executes CollaborationDecision side effects.

Separates the "what to do" (CollaborationStateMachine.decide) from
"how to do it" (this class).  Previously this logic was an if/elif
chain inside OrchestratorRunner._apply_collaboration_signal();
extracting it makes the executor independently testable and the
Runner focused on orchestration flow.
"""

from __future__ import annotations

from typing import Any

from openagents_orchestration.core.collaboration_state_machine import (
    CollaborationAction,
    CollaborationDecision,
)


class CollaborationDecisionExecutor:
    """Executes side effects of a CollaborationDecision.

    Pure side-effect layer — no decision logic.  The caller
    (OrchestratorRunner) wires StateBoard, the resident registry,
    and the task; this class only drives the mechanics.

    Usage::

        decision = csm.decide(task, signal, ...)
        executor = CollaborationDecisionExecutor(board, residents)
        await executor.execute(decision, task, from_id, content)
    """

    def __init__(
        self,
        board: Any,            # StateBoard
        residents: dict[str, Any],  # resident_id → ResidentAgent
    ):
        self._board = board
        self._residents = residents

    async def execute(
        self,
        decision: CollaborationDecision,
        task: Any,
        from_id: str = "",
        content: str = "",
    ) -> bool:
        """Execute a collaboration decision. Returns True if action was taken."""
        action = decision.action

        if action == CollaborationAction.NONE:
            return False

        if action == CollaborationAction.CIRCUIT_BREAK:
            return await self._circuit_break(decision, task)

        if action == CollaborationAction.TRANSITION_TO_REVIEW:
            return await self._transition_to_review(decision, task, from_id, content)

        if action == CollaborationAction.TRANSITION_TO_COMPLETED:
            return await self._transition_to_completed(decision, task, from_id, content)

        if action == CollaborationAction.TRANSITION_TO_FIX_NEEDED:
            return await self._transition_to_fix_needed(decision, task, from_id, content)

        return False

    async def _circuit_break(self, decision: CollaborationDecision, task: Any) -> bool:
        from openagents_orchestration.models.task import TaskStatus

        self._board.update_task(
            decision.task_id, status=TaskStatus.FAILED,
            error=f"Circuit breaker: {decision.reason}",
        )
        task.record_iteration("system", "circuit_breaker", decision.reason)
        self._board.log_event(
            "task.circuit_breaker",
            task_id=decision.task_id,
            message=f"Circuit breaker triggered: {decision.reason}",
        )
        for rid in (decision.producer_id, decision.checker_id):
            resident = self._residents.get(rid)
            if resident is not None:
                await resident.stop()
                self._residents.pop(rid, None)
        task.assigned_agent = ""
        return True

    async def _transition_to_review(
        self, decision: CollaborationDecision, task: Any, from_id: str, content: str,
    ) -> bool:
        from openagents_orchestration.models.task import TaskStatus

        self._board.update_task(decision.task_id, status=TaskStatus.REVIEW)
        # Keep the legacy action name for compatibility with existing tests and
        # dashboards, while the state machine also recognizes the generic name.
        task.record_iteration(from_id, "coder_ready_for_review", content)
        self._board.add_test_report(
            module=decision.task_id,
            result=content,
            passed=decision.tests_passed,
            failed=0,
        )
        # Pause the producer while the checker is working. This prevents the
        # stuck-resident watchdog from killing a producer that is legitimately
        # waiting for feedback, and allows the fix-needed signal to wake it.
        producer = self._residents.get(decision.producer_id)
        if producer is not None:
            await producer.sleep(reason="waiting for checker feedback")
        return True

    async def _transition_to_completed(
        self, decision: CollaborationDecision, task: Any, from_id: str, content: str,
    ) -> bool:
        from openagents_orchestration.models.task import TaskStatus

        self._board.update_task(decision.task_id, status=TaskStatus.COMPLETED)
        task.record_iteration(from_id, "reviewer_approved", content)
        producer = self._residents.get(decision.producer_id)
        checker = self._residents.get(decision.checker_id)
        if producer is not None:
            await producer.stop()
            self._residents.pop(producer.resident_id, None)
        if checker is not None:
            await checker.stop()
            self._residents.pop(checker.resident_id, None)
        task.assigned_agent = ""
        return True

    async def _transition_to_fix_needed(
        self, decision: CollaborationDecision, task: Any, from_id: str, content: str,
    ) -> bool:
        from openagents_orchestration.models.task import TaskStatus

        self._board.update_task(decision.task_id, status=TaskStatus.FIX_NEEDED)
        task.record_iteration(from_id, "reviewer_requested_fix", content)
        self._board.add_error_log(
            source=decision.task_id,
            error=content,
        )
        checker = self._residents.get(decision.checker_id)
        if checker is not None:
            await checker.sleep(reason="waiting for producer fix")
        producer = self._residents.get(decision.producer_id)
        if producer is not None:
            await producer.wake()
        return True
