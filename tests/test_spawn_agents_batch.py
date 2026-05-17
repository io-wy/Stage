"""Tests for spawn_agent batch mode (merged from spawn_agents_batch)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.state_board import StateBoard
from openagents_orchestration.tools.spawn_agent import SpawnAgentTool


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestSpawnAgentBatch:
    @pytest.mark.asyncio
    async def test_batch_spawns_multiple_tasks(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder"),
            ],
        ))

        mock_delegate = AsyncMock(return_value="done")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_ids": ["t1", "t2"]}, ctx)

        assert result["total"] == 2
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        # Verify delegate was called for each task
        assert mock_delegate.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_rejects_not_ready_tasks(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "design", "coder"),
                TaskNode("t2", "implement", "coder", dependencies=["t1"]),
            ],
        ))

        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=AsyncMock()),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        with pytest.raises(Exception, match="not ready"):
            await tool.invoke({"task_ids": ["t2"]}, ctx)

    @pytest.mark.asyncio
    async def test_single_task_mode_still_works(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))

        mock_delegate = AsyncMock(return_value="done")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_id": "t1"}, ctx)

        assert result["status"] == "completed"
        assert result["task_id"] == "t1"

    def test_schema_supports_both_modes(self):
        tool = SpawnAgentTool()
        schema = tool.schema()
        assert "task_id" in schema["properties"]
        assert "task_ids" in schema["properties"]
        assert "required" not in schema
