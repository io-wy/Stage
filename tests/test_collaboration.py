"""Tests for collaborative orchestration state transitions."""

from __future__ import annotations

import pytest

from openagents_orchestration.collaboration import (
    CollaborationSignal,
    parse_collaboration_message,
    task_id_from_resident_id,
)
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.runner import OrchestratorRunner
from openagents_orchestration.state_board import StateBoard


class _ResidentStub:
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


def _runner_with_task(status: TaskStatus = TaskStatus.RUNNING) -> tuple[OrchestratorRunner, StateBoard, TaskNode]:
    board = StateBoard("obj", echo=False)
    task = TaskNode("api-auth", "implement auth", "coder", status=status)
    task.assigned_agent = "coder-api-auth"
    board.add_tasks(TaskGraph(objective="obj", tasks=[task]))
    board.get_or_create_thread("task-api-auth", ["coder-api-auth", "reviewer-api-auth"])

    runner = OrchestratorRunner("agent.json")
    runner._state_board = board
    runner._residents = {
        "coder-api-auth": _ResidentStub("coder-api-auth"),
        "reviewer-api-auth": _ResidentStub("reviewer-api-auth"),
    }
    return runner, board, task


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


def test_collaborative_mode_can_be_forced_on_or_off():
    board = StateBoard("obj", echo=False)
    board.add_tasks(TaskGraph(objective="obj", tasks=[TaskNode("t1", "one coder", "coder")]))

    auto_runner = OrchestratorRunner("agent.json")
    auto_runner._state_board = board
    assert auto_runner._should_use_collaborative_mode() is False

    forced_runner = OrchestratorRunner("agent.json", collaborative_mode="on")
    forced_runner._state_board = board
    assert forced_runner._should_use_collaborative_mode() is True

    disabled_runner = OrchestratorRunner("agent.json", collaborative_mode="off")
    disabled_runner._state_board = board
    assert disabled_runner._should_use_collaborative_mode() is False


@pytest.mark.asyncio
async def test_process_collaborative_messages_approves_task_and_stops_residents():
    runner, board, task = _runner_with_task(TaskStatus.REVIEW)
    thread = board.conversation_threads["task-api-auth"]
    thread.add_message("reviewer-api-auth", "TASK_APPROVED[api-auth]: LGTM", task_id="api-auth")

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.COMPLETED
    assert task.assigned_agent == ""
    assert thread.messages[0]["_processed"] is True
    assert runner._residents == {}
    assert task.iteration_history[-1]["action"] == "reviewer_approved"


@pytest.mark.asyncio
async def test_process_collaborative_messages_requests_fix_once_and_preserves_coder():
    runner, board, task = _runner_with_task(TaskStatus.REVIEW)
    thread = board.conversation_threads["task-api-auth"]
    thread.add_message("reviewer-api-auth", "TASK_FIX_NEEDED[api-auth]: fix auth bug", task_id="api-auth")

    await runner._process_collaborative_messages()
    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.FIX_NEEDED
    assert task.assigned_agent == "coder-api-auth"
    assert thread.messages[0]["_processed"] is True
    assert runner._residents["reviewer-api-auth"]._sleeping is True
    assert "coder-api-auth" in runner._residents
    assert len([h for h in task.iteration_history if h["action"] == "reviewer_requested_fix"]) == 1
    assert board.get_project_context()["recent_errors"][0]["error"].startswith("TASK_FIX_NEEDED")


@pytest.mark.asyncio
async def test_process_collaborative_messages_ignores_wrong_task_marker():
    runner, _board, task = _runner_with_task(TaskStatus.REVIEW)
    thread = runner._state_board.conversation_threads["task-api-auth"]
    thread.add_message("reviewer-api-auth", "TASK_APPROVED[other-task]: LGTM", task_id="api-auth")

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.REVIEW
    assert "_processed" not in thread.messages[0]
