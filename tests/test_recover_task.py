"""Tests for recover_task tool."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.reporting import summarize_agent_run
from openagents_orchestration.tools.director.recover_task import RecoverTaskTool


@pytest.mark.asyncio
async def test_recover_task_creates_minimal_recovery_and_rewires_dependents():
    board = StateBoard("obj", echo=False)
    board.add_tasks(TaskGraph(
        objective="obj",
        tasks=[
            TaskNode("t1", "build API", "coder", expected_artifacts=["api.py"]),
            TaskNode("t2", "test API", "coder", dependencies=["t1"]),
        ],
    ))
    board.update_task("t1", status=TaskStatus.RUNNING)
    board.update_task("t1", status=TaskStatus.FAILED, error="Server disconnected")
    summary = summarize_agent_run(
        agent_id="coder-t1",
        task_id="t1",
        status="failed",
        error="Server disconnected",
        artifacts=["api.py"],
        retry_count=3,
    )
    board.log_event(
        "agent.run_summary",
        task_id="t1",
        agent_id="coder-t1",
        message="failed",
        summary=summary,
    )

    ctx = SimpleNamespace(deps=SimpleNamespace(state_board=board))
    result = await RecoverTaskTool().invoke({"task_id": "t1", "reason": "continue"}, ctx)

    recovery = board.get_task("t1_recovery_1")
    assert recovery is not None
    assert recovery.status == TaskStatus.PENDING
    assert recovery.dependencies == []
    assert "api.py" in recovery.expected_artifacts
    assert "Do not restart from scratch" in recovery.input_context
    assert board.get_task("t2").dependencies == ["t1_recovery_1"]
    assert result["rewired_dependents"] == ["t2"]
    assert result["reusable_artifacts"] == ["api.py"]
