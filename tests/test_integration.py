"""Integration tests for orchestrator end-to-end flows."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.state_board import AgentStatus, Budget, StateBoard
from openagents_orchestration.tools.check_messages import CheckMessagesTool
from openagents_orchestration.tools.finalize import FinalizeTool
from openagents_orchestration.tools.replan import ReplanTool
from openagents_orchestration.tools.send_message import SendMessageTool
from openagents_orchestration.tools.show_state import ShowStateTool
from openagents_orchestration.tools.spawn_agent import SpawnAgentTool


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestSpawnAgentFullChain:
    """Test spawn_agent -> runner_delegate -> StateBoard update."""

    @pytest.mark.asyncio
    async def test_spawn_success_updates_board(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "write hello.py", "coder", input_context="create a hello world script")],
        ))

        mock_delegate = AsyncMock(return_value="Completed.\n\nFILES_CREATED: hello.py")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_id": "t1"}, ctx)

        mock_delegate.assert_awaited_once()
        call_args = mock_delegate.await_args
        assert call_args[1]["agent_type"] == "coder"
        assert "hello.py" in call_args[1]["input_text"]

        assert result["status"] == "completed"
        assert board.get_task("t1").status == TaskStatus.COMPLETED
        assert "hello.py" in board.artifacts
        assert board.agents  # at least one agent registered

    @pytest.mark.asyncio
    async def test_spawn_failure_marks_failed(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))

        mock_delegate = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        with pytest.raises(Exception):
            await tool.invoke({"task_id": "t1"}, ctx)

        assert board.get_task("t1").status == TaskStatus.FAILED
        assert "LLM timeout" in board.get_task("t1").error
        assert "recommendation" in board.get_task("t1").error

    @pytest.mark.asyncio
    async def test_spawn_respects_dependencies(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "design", "coder"),
                TaskNode("t2", "implement", "coder", dependencies=["t1"]),
            ],
        ))

        mock_delegate = AsyncMock(return_value="done")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        # t2 depends on t1, but t1 is not completed -> should fail
        with pytest.raises(Exception, match="unmet dependencies"):
            await tool.invoke({"task_id": "t2"}, ctx)

        mock_delegate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_with_dependency_context(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "design api", "coder", expected_artifacts=["api.yml"]),
                TaskNode("t2", "implement", "coder", dependencies=["t1"]),
            ],
        ))
        board.update_task("t1", status=TaskStatus.COMPLETED, actual_artifacts=["api.yml"])

        mock_delegate = AsyncMock(return_value="done")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        await tool.invoke({"task_id": "t2"}, ctx)

        call_args = mock_delegate.await_args
        input_text = call_args[1]["input_text"]
        # Dependency artifact should be included
        assert "api.yml" in input_text
        assert "Upstream artifacts" in input_text


class TestMessageFlow:
    """Test send_message -> pending -> check_messages full cycle."""

    @pytest.mark.asyncio
    async def test_message_roundtrip(self):
        board = StateBoard("obj")

        # Step 1: Agent A sends message to Agent B
        ctx_a = MockContext(deps=MockContext(state_board=board), agent_id="coder-1")
        send_tool = SendMessageTool()
        await send_tool.invoke({"to_agent": "reviewer-1", "message": "Please check line 42"}, ctx_a)

        assert len(board._pending_messages) == 1

        # Step 2: Agent B checks messages
        ctx_b = MockContext(deps=MockContext(state_board=board), agent_id="reviewer-1")
        check_tool = CheckMessagesTool()
        result = await check_tool.invoke({}, ctx_b)

        assert result["count"] == 1
        assert "line 42" in result["message"]
        # Message should be cleared
        assert len(board._pending_messages) == 0

    @pytest.mark.asyncio
    async def test_broadcast_message(self):
        board = StateBoard("obj")

        ctx_sender = MockContext(deps=MockContext(state_board=board), agent_id="director")
        send_tool = SendMessageTool()
        await send_tool.invoke({"to_agent": "*", "message": "Everyone stop"}, ctx_sender)

        # Any agent should receive the broadcast
        ctx_any = MockContext(deps=MockContext(state_board=board), agent_id="coder-x")
        check_tool = CheckMessagesTool()
        result = await check_tool.invoke({}, ctx_any)

        assert result["count"] == 1
        assert "Everyone stop" in result["message"]

    @pytest.mark.asyncio
    async def test_message_in_spawn_input(self):
        """Pending messages should be injected into spawned agent's input."""
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "fix bug", "coder")],
        ))
        board._pending_messages = [
            {"from": "reviewer", "to": "coder", "content": "The bug is on line 42"},
        ]

        task = board.get_task("t1")
        input_text = SpawnAgentTool._build_input(task, board)

        assert "Messages from other agents" in input_text
        assert "line 42" in input_text


class TestReplanFlow:
    """Test replan tool replaces failed tasks."""

    @pytest.mark.asyncio
    async def test_replan_replaces_task(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "big task", "coder", expected_artifacts=["big.py"]),
                TaskNode("t2", "follow-up", "coder", dependencies=["t1"]),
            ],
        ))

        # Mock LLM that returns sub-tasks
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=MagicMock(
            output_text='{"tasks": [{"task_id": "t1a", "description": "part A", "agent_type": "coder", "expected_artifacts": ["a.py"]}, {"task_id": "t1b", "description": "part B", "agent_type": "coder", "expected_artifacts": ["b.py"]}]}'
        ))

        ctx = MockContext(
            deps=MockContext(state_board=board),
            agent_id="director",
            llm_client=mock_llm,
        )

        tool = ReplanTool()
        result = await tool.invoke({"task_id": "t1", "reason": "too complex"}, ctx)

        assert "t1" in board.tasks
        assert "t1a" in board.tasks
        assert "t1b" in board.tasks
        # t1b should depend on t1a
        assert "t1a" in board.get_task("t1b").dependencies
        # Downstream task should now wait for the replacement chain to finish
        assert board.get_task("t2").dependencies == ["t1b"]
        assert result["rewired_dependents"] == ["t2"]
        assert result["progress"]["ready_tasks"] == 1
        assert board.snapshot()["progress"]["ready_tasks"] == 1


class TestDirectorDecisionFlow:
    """Test Director tools interacting with StateBoard."""

    @pytest.mark.asyncio
    async def test_show_state_then_finalize(self):
        """Director reads state, then finalizes."""
        board = StateBoard("obj", budget=Budget(token_limit=1000))
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder"),
            ],
        ))
        board.update_task("t1", status=TaskStatus.COMPLETED)
        board.update_task("t2", status=TaskStatus.COMPLETED)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")

        # Director reads state
        show_tool = ShowStateTool()
        state = await show_tool.invoke({}, ctx)
        assert "completed" in state

        # Director finalizes
        finalize_tool = FinalizeTool()
        await finalize_tool.invoke({"summary": "All tasks done"}, ctx)

        assert board._final_summary == "All tasks done"
        assert board.all_terminal()

    @pytest.mark.asyncio
    async def test_ask_human_records_question(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder")

        tool = CheckMessagesTool()  # noqa: F841 -- used below but linter may complain
        from openagents_orchestration.tools.ask_human import AskHumanTool
        ask_tool = AskHumanTool()
        result = await ask_tool.invoke({"question": "JWT or session?"}, ctx)

        assert "JWT or session?" in result
        assert len(board._human_questions) == 1
        assert board._human_questions[0]["from"] == "coder"


class TestBudgetTracking:
    """Test budget is tracked across operations."""

    def test_budget_exhaustion(self):
        budget = Budget(token_limit=100, max_steps=2)
        assert not budget.exhausted

        budget.token_used = 100
        assert budget.exhausted

    def test_budget_time_exhaustion(self):
        import time
        budget = Budget(token_limit=10000, time_limit_s=0.01)
        time.sleep(0.02)
        assert budget.exhausted

    def test_budget_steps_exhaustion(self):
        budget = Budget(token_limit=10000, max_steps=3)
        budget.steps_taken = 3
        assert budget.exhausted

    def test_board_tracks_budget(self):
        board = StateBoard("obj", budget=Budget(token_limit=1000))
        board.add_tokens(500)
        assert board.budget.token_used == 500
        assert board.budget.token_remaining == 500

    def test_has_actionable_respects_budget(self):
        board = StateBoard("obj", budget=Budget(token_limit=100))
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        assert board.has_actionable()

        board.add_tokens(100)
        assert not board.has_actionable()
