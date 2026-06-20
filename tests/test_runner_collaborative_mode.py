"""Tests for OrchestratorRunner._run_collaborative scheduling logic.

These tests verify the collaborative loop's dispatch behavior without
spawning real LLM-backed residents.  All LLM / SDK machinery is stubbed
or patched out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class _ResidentStub:
    """Minimal stand-in for ResidentAgent — no LLM, no threads."""

    def __init__(self, resident_id: str):
        self.resident_id = resident_id
        self.stopped = False
        self._sleeping = False
        self._active = True
        self.state = MagicMock()
        self.state.last_active = 0.0
        self.state.status = "idle"

    async def stop(self) -> None:
        self.stopped = True
        self._sleeping = False
        self._active = False

    async def sleep(self, reason: str = "") -> None:
        self._sleeping = True

    async def wake(self) -> None:
        self._sleeping = False

    async def send(self, msg: dict) -> None:
        pass


def _make_runner(*, collab_mode: str = "on") -> OrchestratorRunner:
    """Create a runner with the real agent.json but no network calls."""
    return OrchestratorRunner(
        "agent.json",
        collaborative_mode=collab_mode,
        enable_monitor_resident=False,
    )


def _make_board_with_tasks(*tasks: TaskNode) -> StateBoard:
    """Build a StateBoard seeded with the given tasks."""
    board = StateBoard("test-obj", echo=False)
    board.add_tasks(TaskGraph(objective="test-obj", tasks=list(tasks)))
    return board


# ---------------------------------------------------------------------------
# 1. Ready tasks spawn producers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ready_tasks_spawn_producers():
    """Two PENDING coder tasks (no deps) should trigger _spawn_resident_for_task twice."""
    t1 = TaskNode("t1", "implement auth", "coder")
    t2 = TaskNode("t2", "implement api", "coder")
    board = _make_board_with_tasks(t1, t2)

    runner = _make_runner()
    runner._state_board = board

    # Patch the while-loop condition to exit after one iteration by making
    # budget.exhausted True on the second check.
    exhaustion_toggle = [False]  # list for mutable closure
    original_exhausted = board.budget.exhausted

    @property
    def _exhausted(_self):
        if exhaustion_toggle[0]:
            return True
        exhaustion_toggle[0] = True
        return False

    with patch.object(type(board.budget), "exhausted", _exhausted), \
         patch.object(runner, "_spawn_resident_for_task", new_callable=AsyncMock) as mock_spawn, \
         patch.object(runner, "_process_collaborative_messages", new_callable=AsyncMock), \
         patch.object(runner, "_check_resident_health", new_callable=AsyncMock), \
         patch.object(runner, "_send_fix_to_producer", new_callable=AsyncMock):
        await runner._run_collaborative()

    assert mock_spawn.call_count == 2
    spawned_task_ids = {call.args[0].task_id for call in mock_spawn.call_args_list}
    assert spawned_task_ids == {"t1", "t2"}


# ---------------------------------------------------------------------------
# 2. Review tasks spawn checkers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_tasks_spawn_checkers():
    """A task in REVIEW state should trigger a checker spawn."""
    t1 = TaskNode("t1", "implement auth", "coder", status=TaskStatus.REVIEW)
    t1.assigned_agent = "coder-t1"
    board = _make_board_with_tasks(t1)

    runner = _make_runner()
    runner._state_board = board
    runner._residents = {"coder-t1": _ResidentStub("coder-t1")}

    exhaustion_toggle = [False]

    @property
    def _exhausted(_self):
        if exhaustion_toggle[0]:
            return True
        exhaustion_toggle[0] = True
        return False

    with patch.object(type(board.budget), "exhausted", _exhausted), \
         patch.object(runner, "_spawn_resident_for_task", new_callable=AsyncMock) as mock_spawn, \
         patch.object(runner, "_process_collaborative_messages", new_callable=AsyncMock), \
         patch.object(runner, "_check_resident_health", new_callable=AsyncMock), \
         patch.object(runner, "_send_fix_to_producer", new_callable=AsyncMock):
        await runner._run_collaborative()

    assert mock_spawn.call_count == 1
    call_args = mock_spawn.call_args
    assert call_args.args[0].task_id == "t1"
    assert call_args.args[1] == "reviewer"


# ---------------------------------------------------------------------------
# 3. Fix tasks send fix to producer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fix_tasks_send_fix_to_producer():
    """A FIX_NEEDED task should trigger _send_fix_to_producer."""
    t1 = TaskNode("t1", "implement auth", "coder", status=TaskStatus.FIX_NEEDED)
    t1.assigned_agent = "coder-t1"
    board = _make_board_with_tasks(t1)

    runner = _make_runner()
    runner._state_board = board
    producer_stub = _ResidentStub("coder-t1")
    producer_stub._sleeping = True
    runner._residents = {"coder-t1": producer_stub}

    exhaustion_toggle = [False]

    @property
    def _exhausted(_self):
        if exhaustion_toggle[0]:
            return True
        exhaustion_toggle[0] = True
        return False

    with patch.object(type(board.budget), "exhausted", _exhausted), \
         patch.object(runner, "_spawn_resident_for_task", new_callable=AsyncMock), \
         patch.object(runner, "_process_collaborative_messages", new_callable=AsyncMock), \
         patch.object(runner, "_check_resident_health", new_callable=AsyncMock), \
         patch.object(runner, "_send_fix_to_producer", new_callable=AsyncMock) as mock_fix:
        await runner._run_collaborative()

    assert mock_fix.call_count == 1
    assert mock_fix.call_args.args[0].task_id == "t1"


# ---------------------------------------------------------------------------
# 4. Collab failure falls back to director
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collab_failure_falls_back_to_director():
    """When a task enters FAILED inside the loop, the loop exits without spawning."""
    t1 = TaskNode("t1", "implement auth", "coder", status=TaskStatus.FAILED)
    board = _make_board_with_tasks(t1)

    runner = _make_runner()
    runner._state_board = board

    with patch.object(runner, "_spawn_resident_for_task", new_callable=AsyncMock) as mock_spawn, \
         patch.object(runner, "_process_collaborative_messages", new_callable=AsyncMock), \
         patch.object(runner, "_check_resident_health", new_callable=AsyncMock), \
         patch.object(runner, "_send_fix_to_producer", new_callable=AsyncMock):
        await runner._run_collaborative()

    # The loop should see FAILED, have no ready/review/fix tasks, then detect
    # failed_tasks and break without spawning anything.
    assert mock_spawn.call_count == 0


# ---------------------------------------------------------------------------
# 5. Wake event drives loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wake_event_drives_loop():
    """on_mail_sent hook should set _collab_wake_event so the loop reacts."""
    board = StateBoard("test-obj", echo=False)
    runner = _make_runner()
    runner._state_board = board

    # Wire the hook (normally done in _continue_run)
    board.on_mail_sent(lambda: runner._collab_wake_event.set())

    # Simulate a mail send — the callback should fire.
    assert not runner._collab_wake_event.is_set()
    from openagents_orchestration.models.message import StructuredMessage
    await board.send_structured(
        StructuredMessage.from_text("coder-t1", "reviewer-t1", "hello")
    )
    assert runner._collab_wake_event.is_set()


# ---------------------------------------------------------------------------
# 6. All terminal exits loop immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_terminal_exits_loop():
    """When every task is terminal, _run_collaborative should exit without spawning."""
    t1 = TaskNode("t1", "implement auth", "coder", status=TaskStatus.COMPLETED)
    t2 = TaskNode("t2", "implement api", "coder", status=TaskStatus.COMPLETED)
    board = _make_board_with_tasks(t1, t2)

    runner = _make_runner()
    runner._state_board = board

    with patch.object(runner, "_spawn_resident_for_task", new_callable=AsyncMock) as mock_spawn, \
         patch.object(runner, "_process_collaborative_messages", new_callable=AsyncMock), \
         patch.object(runner, "_check_resident_health", new_callable=AsyncMock), \
         patch.object(runner, "_send_fix_to_producer", new_callable=AsyncMock):
        await runner._run_collaborative()

    assert mock_spawn.call_count == 0
