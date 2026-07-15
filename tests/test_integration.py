"""Integration tests for orchestrator end-to-end flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.core.state_board import Budget, StateBoard
from openagents_orchestration.models.pattern import PatternOutcome, PatternOutcomeStatus
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.tools.director.finalize import FinalizeTool
from openagents_orchestration.tools.director.replan import ReplanTool
from openagents_orchestration.tools.director.show_state import ShowStateTool
from openagents_orchestration.tools.director.spawn_agent import SpawnAgentTool


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestSpawnAgentFullChain:
    """Test spawn_agent -> runner_delegate -> StateBoard update."""

    @pytest.mark.asyncio
    async def test_spawn_success_delegates_and_returns_outcome(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "write hello.py", "coder", input_context="create a hello world script")],
        ))

        # runner_delegate (run_agent) returns a PatternOutcome; the terminal task
        # status is applied by the pattern.after_execute hook, not by this tool.
        mock_delegate = AsyncMock(
            return_value=PatternOutcome(output="Completed.", status=PatternOutcomeStatus.COMPLETED)
        )
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
        # The tool marks the task RUNNING before delegating; flipping it to
        # COMPLETED is the hook's job (not exercised in this unit test).
        assert board.get_task("t1").status == TaskStatus.RUNNING
        assert board.agents  # at least one agent registered

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
        board.update_task("t1", status=TaskStatus.RUNNING)
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
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.COMPLETED)
        board.update_task("t2", status=TaskStatus.RUNNING)
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

        from openagents_orchestration.tools.director.ask_human import AskHumanTool
        ask_tool = AskHumanTool()
        result = await ask_tool.invoke({"question": "JWT or session?"}, ctx)

        assert "JWT or session?" in result
        questions = board.human_channel_service.get_pending_questions(project_id=board.project_id)
        assert len(questions) == 1
        assert questions[0].from_agent == "coder"


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

