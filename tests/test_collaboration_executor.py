"""Tests for CollaborationDecisionExecutor — direct side-effect verification.

These tests call `execute(decision, ...)` directly and assert on the
resulting mutations of StateBoard and resident stubs.  No internal
methods are mocked; the executor is exercised as a black box.
"""

from __future__ import annotations

import pytest

from openagents_orchestration.core.collaboration_executor import (
    CollaborationDecisionExecutor,
)
from openagents_orchestration.core.collaboration_state_machine import (
    CollaborationAction,
    CollaborationDecision,
)
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.transport.channel_policy import ChannelPolicy


# ---------------------------------------------------------------------------
# Resident stub (mirrors test_collaboration.py and test_runner_collaborative_mode.py)
# ---------------------------------------------------------------------------

class _ResidentStub:
    """Minimal stand-in for ResidentAgent — tracks sleep/stop/wake calls."""

    def __init__(self, resident_id: str):
        self.resident_id = resident_id
        self.stopped = False
        self._sleeping = False

    async def stop(self) -> None:
        self.stopped = True
        self._sleeping = False

    async def sleep(self, reason: str = "") -> None:
        self._sleeping = True

    async def wake(self) -> None:
        self._sleeping = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_board_with_task(
    task_id: str = "t1",
    description: str = "test task",
    status: TaskStatus = TaskStatus.RUNNING,
) -> tuple[StateBoard, TaskNode]:
    """Create a StateBoard with a single task in the given status."""
    board = StateBoard(
        "test-obj",
        echo=False,
        channel_policy=ChannelPolicy({"*": {"*"}}),
    )
    task = TaskNode(task_id, description, "coder", status=status)
    task.assigned_agent = f"coder-{task_id}"
    board.add_tasks(TaskGraph(objective="test-obj", tasks=[task]))
    return board, task


def _make_executor(
    board: StateBoard,
    producer_id: str = "coder-t1",
    checker_id: str = "reviewer-t1",
) -> tuple[CollaborationDecisionExecutor, dict[str, _ResidentStub], _ResidentStub, _ResidentStub]:
    """Create an executor with producer + checker stubs.

    Returns (executor, residents_dict, producer_stub, checker_stub) so
    tests can assert on stub state even after the executor pops entries
    from the shared dict.
    """
    producer = _ResidentStub(producer_id)
    checker = _ResidentStub(checker_id)
    residents: dict[str, _ResidentStub] = {
        producer_id: producer,
        checker_id: checker,
    }
    executor = CollaborationDecisionExecutor(board, residents)
    return executor, residents, producer, checker


# ---------------------------------------------------------------------------
# 1. TRANSITION_TO_REVIEW
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_to_review_updates_task_and_sleeps_producer():
    """REVIEW decision: task → REVIEW, producer sleeps, checker untouched."""
    board, task = _make_board_with_task(status=TaskStatus.RUNNING)
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_REVIEW,
        task_id="t1",
        reason="producer signaled review ready",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
        tests_passed=7,
    )

    result = await executor.execute(decision, task, from_id="coder-t1", content="tests ok")

    assert result is True
    assert board.get_task("t1").status == TaskStatus.REVIEW
    assert producer._sleeping is True
    assert checker._sleeping is False
    assert checker.stopped is False

    # Iteration history recorded
    assert task.iteration_history[-1]["action"] == "coder_ready_for_review"
    assert task.iteration_history[-1]["agent_id"] == "coder-t1"

    # Test report recorded when tests_passed is provided
    test_reports = board.project_context["test_reports"]
    assert len(test_reports) == 1
    assert test_reports[0]["passed"] == 7
    assert test_reports[0]["module"] == "t1"


@pytest.mark.asyncio
async def test_transition_to_review_without_tests_passed():
    """REVIEW decision without tests_passed should still work (tests_passed=0)."""
    board, task = _make_board_with_task(status=TaskStatus.RUNNING)
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_REVIEW,
        task_id="t1",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
        tests_passed=0,
    )

    await executor.execute(decision, task, from_id="coder-t1", content="ready")

    assert board.get_task("t1").status == TaskStatus.REVIEW
    # Test report still added with passed=0
    test_reports = board.project_context["test_reports"]
    assert len(test_reports) == 1
    assert test_reports[0]["passed"] == 0


# ---------------------------------------------------------------------------
# 2. TRANSITION_TO_COMPLETED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_to_completed_stops_both_residents():
    """COMPLETED decision: task → COMPLETED, both residents stopped, assigned_agent cleared."""
    board, task = _make_board_with_task(status=TaskStatus.REVIEW)
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_COMPLETED,
        task_id="t1",
        reason="checker approved",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
    )

    result = await executor.execute(decision, task, from_id="reviewer-t1", content="LGTM")

    assert result is True
    assert board.get_task("t1").status == TaskStatus.COMPLETED
    # Residents are popped from executor._residents after stop, so check
    # via the original stub references.
    assert producer.stopped is True
    assert checker.stopped is True
    assert task.assigned_agent == ""

    # Iteration history
    assert task.iteration_history[-1]["action"] == "reviewer_approved"


@pytest.mark.asyncio
async def test_transition_to_completed_with_fix_content_does_not_affect_result():
    """COMPLETED decision should ignore any fix_content — result is final."""
    board, task = _make_board_with_task(status=TaskStatus.REVIEW)
    task.result_output = "original result"
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_COMPLETED,
        task_id="t1",
        reason="checker approved",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
        fix_content="some fix suggestion",  # should be ignored
    )

    await executor.execute(decision, task, from_id="reviewer-t1", content="LGTM")

    assert task.status == TaskStatus.COMPLETED
    # result_output should not be overwritten by fix_content
    assert task.result_output == "original result"


# ---------------------------------------------------------------------------
# 3. TRANSITION_TO_FIX_NEEDED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_to_fix_needed_sleeps_checker_wakes_producer():
    """FIX_NEEDED decision: task → FIX_NEEDED, checker sleeps, producer wakes."""
    board, task = _make_board_with_task(status=TaskStatus.REVIEW)
    executor, residents, producer, checker = _make_executor(board)
    producer._sleeping = True  # producer was sleeping

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_FIX_NEEDED,
        task_id="t1",
        reason="checker requested fixes",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
        fix_content="line 42 missing null check",
    )

    result = await executor.execute(
        decision, task, from_id="reviewer-t1", content="line 42 missing null check"
    )

    assert result is True
    assert board.get_task("t1").status == TaskStatus.FIX_NEEDED
    assert checker._sleeping is True
    assert producer._sleeping is False  # woke up

    # Iteration history
    assert task.iteration_history[-1]["action"] == "reviewer_requested_fix"

    # Error log recorded
    error_logs = board.project_context["error_logs"]
    assert len(error_logs) >= 1
    assert error_logs[-1]["error"] == "line 42 missing null check"
    assert error_logs[-1]["source"] == "t1"


# ---------------------------------------------------------------------------
# 4. CIRCUIT_BREAK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_break_fails_task_stops_both_residents():
    """CIRCUIT_BREAK: task → FAILED, both residents stopped, error logged."""
    board, task = _make_board_with_task(status=TaskStatus.FIX_NEEDED)
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.CIRCUIT_BREAK,
        task_id="t1",
        reason="5 fix iterations (max=5)",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
    )

    result = await executor.execute(decision, task)

    assert result is True
    assert board.get_task("t1").status == TaskStatus.FAILED
    # Residents are popped from executor._residents after stop — check via
    # the original stub references.
    assert producer.stopped is True
    assert checker.stopped is True
    assert task.assigned_agent == ""

    # Error should mention circuit breaker
    assert task.error is not None
    assert "circuit breaker" in task.error.lower() or "Circuit breaker" in task.error

    # Iteration history
    assert task.iteration_history[-1]["action"] == "circuit_breaker"

    # Event logged
    events = [e for e in board.events if e.event_type == "task.circuit_breaker"]
    assert len(events) == 1
    assert "circuit breaker" in events[0].message.lower()


# ---------------------------------------------------------------------------
# 5. NONE decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_none_decision_leaves_everything_unchanged():
    """NONE decision: no mutations to board or residents."""
    board, task = _make_board_with_task(status=TaskStatus.RUNNING)
    executor, residents, producer, checker = _make_executor(board)

    # Capture pre-state
    pre_status = task.status
    pre_history_len = len(task.iteration_history)
    pre_events_len = len(board.events)
    pre_sleeping = {rid: r._sleeping for rid, r in residents.items()}
    pre_stopped = {rid: r.stopped for rid, r in residents.items()}

    decision = CollaborationDecision(
        action=CollaborationAction.NONE,
        task_id="t1",
    )

    result = await executor.execute(decision, task)

    assert result is False
    assert task.status == pre_status
    assert len(task.iteration_history) == pre_history_len
    assert len(board.events) == pre_events_len
    for rid in residents:
        assert residents[rid]._sleeping == pre_sleeping[rid]
        assert residents[rid].stopped == pre_stopped[rid]


# ---------------------------------------------------------------------------
# 6. Boundary: residents already removed from _residents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_to_completed_when_residents_already_removed():
    """COMPLETED with missing residents: task still updates, no exception."""
    board, task = _make_board_with_task(status=TaskStatus.REVIEW)
    # Empty residents dict — both producer and checker are gone
    empty_residents: dict[str, _ResidentStub] = {}
    executor = CollaborationDecisionExecutor(board, empty_residents)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_COMPLETED,
        task_id="t1",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
    )

    # Should not raise
    result = await executor.execute(decision, task, from_id="reviewer-t1", content="LGTM")

    assert result is True
    assert board.get_task("t1").status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_circuit_break_when_producer_already_removed():
    """CIRCUIT_BREAK with only checker present: task fails, checker stopped, no exception."""
    board, task = _make_board_with_task(status=TaskStatus.FIX_NEEDED)
    checker = _ResidentStub("reviewer-t1")
    partial_residents: dict[str, _ResidentStub] = {"reviewer-t1": checker}
    executor = CollaborationDecisionExecutor(board, partial_residents)

    decision = CollaborationDecision(
        action=CollaborationAction.CIRCUIT_BREAK,
        task_id="t1",
        reason="max iterations reached",
        producer_id="coder-t1",  # missing from residents
        checker_id="reviewer-t1",
    )

    result = await executor.execute(decision, task)

    assert result is True
    assert board.get_task("t1").status == TaskStatus.FAILED
    assert checker.stopped is True


@pytest.mark.asyncio
async def test_transition_to_fix_needed_when_checker_already_removed():
    """FIX_NEEDED with missing checker: task still updates, producer wakes, no exception."""
    board, task = _make_board_with_task(status=TaskStatus.REVIEW)
    producer = _ResidentStub("coder-t1")
    producer._sleeping = True
    partial_residents: dict[str, _ResidentStub] = {"coder-t1": producer}
    executor = CollaborationDecisionExecutor(board, partial_residents)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_FIX_NEEDED,
        task_id="t1",
        producer_id="coder-t1",
        checker_id="reviewer-t1",  # missing from residents
        fix_content="missing null check",
    )

    result = await executor.execute(decision, task, from_id="reviewer-t1", content="missing null check")

    assert result is True
    assert board.get_task("t1").status == TaskStatus.FIX_NEEDED
    assert producer._sleeping is False  # still woke up


@pytest.mark.asyncio
async def test_transition_to_review_when_producer_already_removed():
    """REVIEW with missing producer: task still updates, no exception."""
    board, task = _make_board_with_task(status=TaskStatus.RUNNING)
    checker = _ResidentStub("reviewer-t1")
    partial_residents: dict[str, _ResidentStub] = {"reviewer-t1": checker}
    executor = CollaborationDecisionExecutor(board, partial_residents)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_REVIEW,
        task_id="t1",
        producer_id="coder-t1",  # missing
        checker_id="reviewer-t1",
    )

    result = await executor.execute(decision, task, from_id="coder-t1", content="ready")

    assert result is True
    assert board.get_task("t1").status == TaskStatus.REVIEW
    assert checker._sleeping is False  # checker untouched


# ---------------------------------------------------------------------------
# 7. Additional edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_to_completed_removes_residents_from_registry():
    """COMPLETED should pop residents from the executor's _residents dict."""
    board, task = _make_board_with_task(status=TaskStatus.REVIEW)
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.TRANSITION_TO_COMPLETED,
        task_id="t1",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
    )

    await executor.execute(decision, task, from_id="reviewer-t1", content="LGTM")

    # The executor's internal _residents dict should have both removed
    assert "coder-t1" not in executor._residents
    assert "reviewer-t1" not in executor._residents


@pytest.mark.asyncio
async def test_circuit_break_removes_residents_from_registry():
    """CIRCUIT_BREAK should pop residents from the executor's _residents dict."""
    board, task = _make_board_with_task(status=TaskStatus.FIX_NEEDED)
    executor, residents, producer, checker = _make_executor(board)

    decision = CollaborationDecision(
        action=CollaborationAction.CIRCUIT_BREAK,
        task_id="t1",
        producer_id="coder-t1",
        checker_id="reviewer-t1",
    )

    await executor.execute(decision, task)

    assert "coder-t1" not in executor._residents
    assert "reviewer-t1" not in executor._residents


@pytest.mark.asyncio
async def test_unrecognized_action_returns_false():
    """An unknown action should return False and make no changes."""
    board, task = _make_board_with_task(status=TaskStatus.RUNNING)
    executor, residents, producer, checker = _make_executor(board)

    # Use SLEEP_REVIEWER which exists in enum but has no handler
    decision = CollaborationDecision(
        action=CollaborationAction.SLEEP_REVIEWER,
        task_id="t1",
    )

    pre_status = task.status
    result = await executor.execute(decision, task)

    assert result is False
    assert task.status == pre_status
