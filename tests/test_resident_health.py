"""Tests for resident health monitoring in collaborative mode."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class _ResidentStub:
    """Minimal stand-in for ResidentAgent."""

    def __init__(self, resident_id: str, *, last_active: float = 0.0, status: str = "idle"):
        self.resident_id = resident_id
        self.stopped = False
        self._sleeping = False
        self._active = True
        self.state = MagicMock()
        self.state.last_active = last_active
        self.state.status = status
        self.bound_task_id = resident_id.replace("coder-", "").replace("reviewer-", "")

    async def stop(self) -> None:
        self.stopped = True
        self._active = False


def _make_runner(stuck_threshold_s: float = 1.0) -> OrchestratorRunner:
    return OrchestratorRunner(
        "agent.json",
        collaborative_mode="on",
        enable_monitor_resident=False,
        resident_stuck_threshold_s=stuck_threshold_s,
    )


@pytest.mark.asyncio
async def test_stuck_resident_marks_task_failed_and_stops():
    """A resident idle longer than threshold should fail its task and stop."""
    import time

    board = StateBoard("test-obj", echo=False)
    board.add_tasks(
        TaskGraph(
            objective="test-obj",
            tasks=[TaskNode("t1", "task", "coder", status=TaskStatus.RUNNING)],
        )
    )

    runner = _make_runner(stuck_threshold_s=0.1)
    runner._state_board = board
    runner._residents = {
        "coder-t1": _ResidentStub(
            "coder-t1",
            last_active=time.time() - 10.0,
            status="idle",
        ),
    }

    await runner._check_resident_health()

    assert board.get_task("t1").status == TaskStatus.FAILED
    assert "stuck" in board.get_task("t1").error
    assert runner._residents["coder-t1"].stopped
    assert any(e.event_type == "orchestrator.resident_stuck" for e in board.events)


@pytest.mark.asyncio
async def test_sleeping_resident_not_flagged_as_stuck():
    """Sleeping residents are intentionally idle and should not be marked stuck."""
    import time

    board = StateBoard("test-obj", echo=False)
    board.add_tasks(
        TaskGraph(
            objective="test-obj",
            tasks=[TaskNode("t1", "task", "coder", status=TaskStatus.REVIEW)],
        )
    )

    runner = _make_runner(stuck_threshold_s=0.1)
    runner._state_board = board
    runner._residents = {
        "reviewer-t1": _ResidentStub(
            "reviewer-t1",
            last_active=time.time() - 10.0,
            status="sleeping",
        ),
    }

    await runner._check_resident_health()

    assert board.get_task("t1").status == TaskStatus.REVIEW
    assert not runner._residents["reviewer-t1"].stopped
    assert not any(e.event_type == "orchestrator.resident_stuck" for e in board.events)


@pytest.mark.asyncio
async def test_error_resident_marks_task_failed_and_stops():
    """A resident in error state should be stopped regardless of idle time."""
    board = StateBoard("test-obj", echo=False)
    board.add_tasks(
        TaskGraph(
            objective="test-obj",
            tasks=[TaskNode("t1", "task", "coder", status=TaskStatus.RUNNING)],
        )
    )

    runner = _make_runner(stuck_threshold_s=1000.0)
    runner._state_board = board
    runner._residents = {
        "coder-t1": _ResidentStub(
            "coder-t1",
            last_active=0.0,
            status="error",
        ),
    }

    await runner._check_resident_health()

    assert board.get_task("t1").status == TaskStatus.FAILED
    assert runner._residents["coder-t1"].stopped


@pytest.mark.asyncio
async def test_recently_active_resident_not_flagged():
    """A resident active within threshold should not be touched."""
    import time

    board = StateBoard("test-obj", echo=False)
    board.add_tasks(
        TaskGraph(
            objective="test-obj",
            tasks=[TaskNode("t1", "task", "coder", status=TaskStatus.RUNNING)],
        )
    )

    runner = _make_runner(stuck_threshold_s=60.0)
    runner._state_board = board
    runner._residents = {
        "coder-t1": _ResidentStub(
            "coder-t1",
            last_active=time.time(),
            status="busy",
        ),
    }

    await runner._check_resident_health()

    assert board.get_task("t1").status == TaskStatus.RUNNING
    assert not runner._residents["coder-t1"].stopped


@pytest.mark.asyncio
async def test_resident_without_task_is_stopped_but_no_task_update():
    """A stuck resident with no bound task should stop without crashing."""
    import time

    board = StateBoard("test-obj", echo=False)

    runner = _make_runner(stuck_threshold_s=0.1)
    runner._state_board = board
    resident = _ResidentStub(
        "coder-t1",
        last_active=time.time() - 10.0,
        status="idle",
    )
    resident.bound_task_id = None
    runner._residents = {"coder-t1": resident}

    await runner._check_resident_health()

    assert resident.stopped
    # No task update should occur because there is no bound task
    assert not any(e.event_type.startswith("task.") for e in board.events)
