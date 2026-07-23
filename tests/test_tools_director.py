"""Tests for Director tools: show_state, spawn_agent, finalize, replan, ask_human."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openagents.errors.exceptions import PermanentToolError

from openagents_orchestration.models.pattern import (
    PatternError,
    PatternOutcome,
    PatternOutcomeStatus,
)
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.runtime.state_board import StateBoard
from openagents_orchestration.tools.director.ask_human import AskHumanTool
from openagents_orchestration.tools.director.decompose import DecomposeTool
from openagents_orchestration.tools.director.finalize import FinalizeTool
from openagents_orchestration.tools.director.replan import ReplanTool
from openagents_orchestration.tools.director.show_state import ShowStateTool
from openagents_orchestration.tools.director.spawn_agent import SpawnAgentTool


class MockContext:
    """Minimal context mock matching the pattern used in test_integration.py."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# ShowStateTool
# ---------------------------------------------------------------------------

class TestShowStateTool:
    def test_schema_has_section_field(self):
        tool = ShowStateTool()
        schema = tool.schema()
        assert "properties" in schema
        assert "section" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_returns_snapshot_json(self):
        board = StateBoard("test-obj")
        board.add_tasks(TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "write hello.py", "coder")],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")

        tool = ShowStateTool()
        result = await tool.invoke({}, ctx)
        assert "test-obj" in result
        assert "tasks" in result

    @pytest.mark.asyncio
    async def test_invoke_section_filter(self):
        board = StateBoard("test-obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")

        tool = ShowStateTool()
        result = await tool.invoke({"section": "budget"}, ctx)
        assert "token_limit" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = ShowStateTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({}, ctx)


# ---------------------------------------------------------------------------
# SpawnAgentTool
# ---------------------------------------------------------------------------

class TestSpawnAgentTool:
    def test_schema_has_task_id_and_task_ids(self):
        tool = SpawnAgentTool()
        schema = tool.schema()
        assert "task_id" in schema["properties"]
        assert "task_ids" in schema["properties"]
        assert "agent_spec" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_success_starts_agent_and_returns_outcome(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "write hello.py", "coder")],
        ))

        mock_delegate = AsyncMock(
            return_value=PatternOutcome(
                output="Completed.",
                status=PatternOutcomeStatus.COMPLETED,
            )
        )
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_id": "t1"}, ctx)

        assert result["status"] == "completed"
        assert board.get_task("t1").status == TaskStatus.RUNNING
        assert board.agents  # at least one agent registered
        mock_delegate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invoke_failure_returns_failed_outcome(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))

        mock_delegate = AsyncMock(
            return_value=PatternOutcome(
                output="",
                status=PatternOutcomeStatus.FAILED,
                error=PatternError(message="LLM timeout"),
            )
        )
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_id": "t1"}, ctx)

        assert result["status"] == "failed"
        assert "LLM timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_invoke_missing_task_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=AsyncMock()),
            agent_id="director",
        )
        tool = SpawnAgentTool()
        with pytest.raises(PermanentToolError, match="not found"):
            await tool.invoke({"task_id": "missing"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(runner_delegate=AsyncMock()), agent_id="director")
        tool = SpawnAgentTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"task_id": "t1"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_runner_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = SpawnAgentTool()
        with pytest.raises(PermanentToolError, match="Runner"):
            await tool.invoke({"task_id": "t1"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_missing_task_id_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=AsyncMock()),
            agent_id="director",
        )
        tool = SpawnAgentTool()
        with pytest.raises(PermanentToolError, match="task_id"):
            await tool.invoke({}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_batch_mode(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder"),
            ],
        ))

        mock_delegate = AsyncMock(
            return_value=PatternOutcome(
                output="done",
                status=PatternOutcomeStatus.COMPLETED,
            )
        )
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_ids": ["t1", "t2"]}, ctx)

        assert result["total"] == 2
        assert result["succeeded"] == 2

    @pytest.mark.asyncio
    async def test_invoke_respects_dependencies(self):
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
        with pytest.raises(PermanentToolError, match="unmet dependencies"):
            await tool.invoke({"task_id": "t2"}, ctx)

        mock_delegate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invoke_with_dependency_context(self):
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
        assert "api.yml" in input_text
        assert "Upstream artifacts" in input_text

    @pytest.mark.asyncio
    async def test_invoke_with_agent_spec_compiles_inline_role(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "audit auth flow", "coder")],
        ))

        mock_delegate = AsyncMock(
            return_value=PatternOutcome(
                output="done",
                status=PatternOutcomeStatus.COMPLETED,
            )
        )
        fake_runner = MockContext(
            _config_path=Path("agent.json"),
            _agents_by_id={},
            _bundles={},
        )
        ctx = MockContext(
            deps=MockContext(
                state_board=board,
                runner_delegate=mock_delegate,
                runner=fake_runner,
            ),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke(
            {
                "task_id": "t1",
                "agent_spec": {
                    "id": "security-auditor",
                    "extends": "_base.json",
                    "tools": ["+grep"],
                },
            },
            ctx,
        )

        assert result["status"] == "completed"
        assert "security-auditor" in fake_runner._agents_by_id
        # The spawned agent_id should use the inline role id.
        assert board.get_agent("security-auditor-t1") is not None

    @pytest.mark.asyncio
    async def test_invoke_agent_spec_rejected_in_batch_mode(self):
        board = StateBoard("obj")
        ctx = MockContext(
            deps=MockContext(
                state_board=board,
                runner_delegate=AsyncMock(),
                runner=MockContext(_config_path=Path("agent.json"), _agents_by_id={}, _bundles={}),
            ),
            agent_id="director",
        )
        tool = SpawnAgentTool()
        with pytest.raises(PermanentToolError, match="agent_spec is only valid"):
            await tool.invoke(
                {
                    "task_ids": ["t1", "t2"],
                    "agent_spec": {"id": "x", "extends": "_base.json"},
                },
                ctx,
            )

    @pytest.mark.asyncio
    async def test_invoke_with_context_appends_director_notes(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "write hello.py", "coder")],
        ))

        mock_delegate = AsyncMock(return_value="Completed.")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        await tool.invoke(
            {"task_id": "t1", "context": "Use single quotes and add a docstring."},
            ctx,
        )

        input_text = mock_delegate.await_args[1]["input_text"]
        assert "Additional instructions from the Director" in input_text
        assert "Use single quotes and add a docstring." in input_text


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

class TestFinalizeTool:
    def test_schema_has_summary(self):
        tool = FinalizeTool()
        schema = tool.schema()
        assert "summary" in schema["properties"]
        assert schema.get("required") == ["summary"]

    @pytest.mark.asyncio
    async def test_invoke_sets_final_summary(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")

        tool = FinalizeTool()
        result = await tool.invoke({"summary": "All tasks done"}, ctx)

        assert board._final_summary == "All tasks done"
        assert result == "All tasks done"

    @pytest.mark.asyncio
    async def test_invoke_missing_summary_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = FinalizeTool()
        with pytest.raises(PermanentToolError, match="summary"):
            await tool.invoke({"summary": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = FinalizeTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"summary": "done"}, ctx)


# ---------------------------------------------------------------------------
# ReplanTool
# ---------------------------------------------------------------------------

class TestReplanTool:
    def test_schema_has_required_fields(self):
        tool = ReplanTool()
        schema = tool.schema()
        assert "task_id" in schema["properties"]
        assert "reason" in schema["properties"]
        assert schema.get("required") == ["task_id", "reason"]

    @pytest.mark.asyncio
    async def test_invoke_replaces_task(self):
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
            output_text='{"tasks": [{"task_id": "t1a", "description": "part A", "agent_type": "coder"}, {"task_id": "t1b", "description": "part B", "agent_type": "coder"}]}'
        ))

        ctx = MockContext(
            deps=MockContext(state_board=board),
            agent_id="director",
            llm_client=mock_llm,
        )

        # Patch structured_generate to return our mock data directly
        with patch(
            "openagents_orchestration.tools.director.replan.structured_generate",
            new_callable=AsyncMock,
        ) as mock_structured:
            from openagents_orchestration.tools.director.replan import (
                _ReplanOutputSchema,
                _ReplanTaskSchema,
            )
            mock_structured.return_value = (
                _ReplanOutputSchema(tasks=[
                    _ReplanTaskSchema(task_id="t1a", description="part A", agent_type="coder"),
                    _ReplanTaskSchema(task_id="t1b", description="part B", agent_type="coder"),
                ]),
                None,
            )

            tool = ReplanTool()
            result = await tool.invoke({"task_id": "t1", "reason": "too complex"}, ctx)

        assert "t1a" in board.tasks
        assert "t1b" in board.tasks
        # t1b should depend on t1a
        assert "t1a" in board.get_task("t1b").dependencies
        # Downstream task should now wait for the replacement chain
        assert board.get_task("t2").dependencies == ["t1b"]
        assert result["rewired_dependents"] == ["t2"]
        assert "progress" in result

    @pytest.mark.asyncio
    async def test_invoke_missing_params_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = ReplanTool()
        with pytest.raises(PermanentToolError, match="required"):
            await tool.invoke({"task_id": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_task_not_found_raises(self):
        board = StateBoard("obj")
        mock_llm = MagicMock()
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director", llm_client=mock_llm)
        tool = ReplanTool()
        with pytest.raises(PermanentToolError, match="not found"):
            await tool.invoke({"task_id": "missing", "reason": "test"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = ReplanTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"task_id": "t1", "reason": "test"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_llm_raises(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = ReplanTool()
        with pytest.raises(PermanentToolError, match="LLM"):
            await tool.invoke({"task_id": "t1", "reason": "test"}, ctx)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

class TestAskHumanTool:
    def test_schema_has_required_fields(self):
        tool = AskHumanTool()
        schema = tool.schema()
        assert "question" in schema["properties"]
        assert "options" in schema["properties"]
        assert schema.get("required") == ["question"]

    @pytest.mark.asyncio
    async def test_invoke_records_question(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder")

        tool = AskHumanTool()
        result = await tool.invoke({"question": "JWT or session?"}, ctx)

        assert "JWT or session?" in result
        questions = board.human_channel_service.get_pending_questions(project_id=board.project_id)
        assert len(questions) == 1
        assert questions[0].from_agent == "coder"

    @pytest.mark.asyncio
    async def test_invoke_with_options(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder")

        tool = AskHumanTool()
        result = await tool.invoke({"question": "Which one?", "options": "A, B, C"}, ctx)

        assert "Options: A, B, C" in result

    @pytest.mark.asyncio
    async def test_invoke_missing_question_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder")
        tool = AskHumanTool()
        with pytest.raises(PermanentToolError, match="question"):
            await tool.invoke({"question": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="coder")
        tool = AskHumanTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"question": "test?"}, ctx)


# ---------------------------------------------------------------------------
# DecomposeTool
# ---------------------------------------------------------------------------


class TestDecomposeTool:
    @pytest.mark.asyncio
    async def test_invoke_with_subtasks_does_not_crash(self):
        """Regression for the AttributeError on task.subtasks before TaskNode had the field."""
        board = StateBoard("obj")
        mock_llm = MagicMock()
        ctx = MockContext(
            deps=MockContext(state_board=board),
            agent_id="director",
            llm_client=mock_llm,
        )

        with patch(
            "openagents_orchestration.tools.director.decompose.structured_generate",
            new_callable=AsyncMock,
        ) as mock_structured:
            from openagents_orchestration.tools.director.decompose import (
                _GraphSchema,
                _SubtaskSchema,
                _TaskSchema,
            )

            mock_structured.return_value = (
                _GraphSchema(
                    tasks=[
                        _TaskSchema(
                            task_id="t1",
                            description="parent",
                            agent_type="coder",
                            subtasks=[
                                _SubtaskSchema(
                                    task_id="t1a",
                                    description="sub A",
                                    agent_type="coder",
                                ),
                                _SubtaskSchema(
                                    task_id="t1b",
                                    description="sub B",
                                    agent_type="coder",
                                    dependencies=["t1a"],
                                ),
                            ],
                        )
                    ]
                ),
                None,
            )

            tool = DecomposeTool()
            result = await tool.invoke({"objective": "build x"}, ctx)

        assert result["tasks_added"] == 1
        parent = board.get_task("t1")
        assert parent is not None
        assert len(parent.subtasks) == 2
        assert parent.subtasks[0].task_id == "t1a"
        assert parent.subtasks[1].task_id == "t1b"
        assert parent.subtasks[1].dependencies == ["t1a"]

    @pytest.mark.asyncio
    async def test_simple_intent_returns_single_task_without_llm(self):
        """Simple intents must short-circuit to a single task — no LLM call, no over-split."""
        board = StateBoard("obj")
        ctx = MockContext(
            deps=MockContext(state_board=board),
            agent_id="director",
            llm_client=MagicMock(),  # should not be called
        )

        tool = DecomposeTool()
        result = await tool.invoke(
            {
                "objective": "write a greet function",
                "intent": {
                    "task_type": "feature",
                    "complexity": "simple",
                    "external": [],
                    "priority": "normal",
                    "confidence": 0.9,
                    "reason": "function implementation",
                    "source": "L1_rule",
                },
            },
            ctx,
        )

        assert result["tasks_added"] == 1
        assert result["task_ids"] == ["t1"]
        task = board.get_task("t1")
        assert task is not None
        assert task.description == "write a greet function"
        assert task.agent_type == "coder"
        assert task.dependencies == []
        assert ctx.llm_client.call_count == 0

    @pytest.mark.asyncio
    async def test_invoke_no_objective_returns_error(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = DecomposeTool()
        result = await tool.invoke({"objective": ""}, ctx)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invoke_no_board_returns_error(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = DecomposeTool()
        result = await tool.invoke({"objective": "build x"}, ctx)
        assert "error" in result
        assert "StateBoard" in result["error"]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
