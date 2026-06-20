"""Tests for Director tools: show_state, spawn_agent, send_message, finalize,
replan, recover_task, correct_task_status, ask_human, check_messages."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagents.errors.exceptions import PermanentToolError, RetryableToolError
from openagents_orchestration.core.state_board import AgentStatus, Budget, StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.tools.director.ask_human import AskHumanTool
from openagents_orchestration.tools.director.check_messages import CheckMessagesTool
from openagents_orchestration.tools.director.correct_task_status import CorrectTaskStatusTool
from openagents_orchestration.tools.director.finalize import FinalizeTool
from openagents_orchestration.tools.director.recover_task import RecoverTaskTool
from openagents_orchestration.tools.director.replan import ReplanTool
from openagents_orchestration.tools.director.send_message import SendMessageTool
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

    @pytest.mark.asyncio
    async def test_invoke_with_dlq(self):
        board = StateBoard("test-obj")
        board.register_agent("agent-a", "coder")
        # Create a mailbox by sending a message
        from openagents_orchestration.models.message import StructuredMessage
        msg = StructuredMessage.from_text("director", "agent-a", "test")
        await board.send_structured(msg)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = ShowStateTool()
        result = await tool.invoke({}, ctx)
        assert "test-obj" in result


# ---------------------------------------------------------------------------
# SpawnAgentTool
# ---------------------------------------------------------------------------

class TestSpawnAgentTool:
    def test_schema_has_task_id_and_task_ids(self):
        tool = SpawnAgentTool()
        schema = tool.schema()
        assert "task_id" in schema["properties"]
        assert "task_ids" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_success_updates_board(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "write hello.py", "coder")],
        ))

        mock_delegate = AsyncMock(return_value="Completed.\n\nFILES_CREATED: hello.py")
        ctx = MockContext(
            deps=MockContext(state_board=board, runner_delegate=mock_delegate),
            agent_id="director",
        )

        tool = SpawnAgentTool()
        result = await tool.invoke({"task_id": "t1"}, ctx)

        assert result["status"] == "completed"
        assert board.get_task("t1").status == TaskStatus.COMPLETED
        assert board.agents  # at least one agent registered
        mock_delegate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invoke_failure_marks_failed(self):
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
        with pytest.raises(RetryableToolError):
            await tool.invoke({"task_id": "t1"}, ctx)

        assert board.get_task("t1").status == TaskStatus.FAILED
        assert "LLM timeout" in board.get_task("t1").error

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

        mock_delegate = AsyncMock(return_value="done")
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


# ---------------------------------------------------------------------------
# SendMessageTool
# ---------------------------------------------------------------------------

class TestSendMessageTool:
    def test_schema_has_required_fields(self):
        tool = SendMessageTool()
        schema = tool.schema()
        assert "to_agent" in schema["properties"]
        assert "message" in schema["properties"]
        assert schema.get("required") == ["to_agent", "message"]

    @pytest.mark.asyncio
    async def test_invoke_sends_message(self):
        board = StateBoard("obj")
        board.register_agent("reviewer-1", "reviewer")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder-1")
        tool = SendMessageTool()
        result = await tool.invoke({"to_agent": "reviewer-1", "message": "Please check line 42"}, ctx)

        assert "sent" in result.lower() or "delivered" in result.lower()
        pending = await board.peek_mailbox("reviewer-1")
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_invoke_broadcast(self):
        board = StateBoard("obj")
        board.register_agent("coder-x", "coder")
        board.register_agent("reviewer-x", "reviewer")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = SendMessageTool()
        result = await tool.invoke({"to_agent": "*", "message": "Everyone stop"}, ctx)

        assert "sent" in result.lower() or "delivered" in result.lower()

    @pytest.mark.asyncio
    async def test_invoke_missing_params_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = SendMessageTool()
        with pytest.raises(PermanentToolError, match="required"):
            await tool.invoke({"to_agent": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = SendMessageTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"to_agent": "x", "message": "hi"}, ctx)


# ---------------------------------------------------------------------------
# FinalizeTool
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
            from openagents_orchestration.tools.director.replan import _ReplanOutputSchema, _ReplanTaskSchema
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
# RecoverTaskTool
# ---------------------------------------------------------------------------

class TestRecoverTaskTool:
    def test_schema_has_required_fields(self):
        tool = RecoverTaskTool()
        schema = tool.schema()
        assert "task_id" in schema["properties"]
        assert schema.get("required") == ["task_id"]

    @pytest.mark.asyncio
    async def test_invoke_creates_recovery_task(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "big task", "coder", expected_artifacts=["big.py"]),
                TaskNode("t2", "follow-up", "coder", dependencies=["t1"]),
            ],
        ))
        # Must go through running first due to state machine validation
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.FAILED, error="something went wrong")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = RecoverTaskTool()
        result = await tool.invoke({"task_id": "t1", "reason": "try again"}, ctx)

        assert "recovery_task" in result
        recovery_id = result["recovery_task"]
        assert recovery_id in board.tasks
        assert board.get_task("t2").dependencies == [recovery_id]
        assert result["rewired_dependents"] == ["t2"]

    @pytest.mark.asyncio
    async def test_invoke_task_not_failed_raises(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = RecoverTaskTool()
        with pytest.raises(PermanentToolError, match="not failed"):
            await tool.invoke({"task_id": "t1"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_task_not_found_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = RecoverTaskTool()
        with pytest.raises(PermanentToolError, match="not found"):
            await tool.invoke({"task_id": "missing"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = RecoverTaskTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({"task_id": "t1"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_missing_task_id_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = RecoverTaskTool()
        with pytest.raises(PermanentToolError, match="task_id"):
            await tool.invoke({"task_id": ""}, ctx)


# ---------------------------------------------------------------------------
# CorrectTaskStatusTool
# ---------------------------------------------------------------------------

class TestCorrectTaskStatusTool:
    def test_schema_has_required_fields(self):
        tool = CorrectTaskStatusTool()
        schema = tool.schema()
        assert "task_id" in schema["properties"]
        assert "status" in schema["properties"]
        assert "reason" in schema["properties"]
        assert "artifacts" in schema["properties"]
        assert schema.get("required") == ["task_id", "status", "reason"]

    @pytest.mark.asyncio
    async def test_invoke_corrects_status(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        # Must go through running first due to state machine validation
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.FAILED, error="oops")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = CorrectTaskStatusTool()
        result = await tool.invoke({
            "task_id": "t1",
            "status": "completed",
            "reason": "verified externally",
        }, ctx)

        assert result["old_status"] == "failed"
        assert result["new_status"] == "completed"
        assert board.get_task("t1").status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_invoke_noop_same_status(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        # Must go through running first due to state state machine validation
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.COMPLETED)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = CorrectTaskStatusTool()
        result = await tool.invoke({
            "task_id": "t1",
            "status": "completed",
            "reason": "already done",
        }, ctx)

        assert result["note"] == "Status unchanged, artifacts updated"

    @pytest.mark.asyncio
    async def test_invoke_invalid_status_raises(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = CorrectTaskStatusTool()
        with pytest.raises(PermanentToolError, match="Invalid"):
            await tool.invoke({
                "task_id": "t1",
                "status": "not_a_status",
                "reason": "test",
            }, ctx)

    @pytest.mark.asyncio
    async def test_invoke_task_not_found_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = CorrectTaskStatusTool()
        with pytest.raises(PermanentToolError, match="not found"):
            await tool.invoke({
                "task_id": "missing",
                "status": "completed",
                "reason": "test",
            }, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = CorrectTaskStatusTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({
                "task_id": "t1",
                "status": "completed",
                "reason": "test",
            }, ctx)

    @pytest.mark.asyncio
    async def test_invoke_missing_params_raises(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")
        tool = CorrectTaskStatusTool()
        with pytest.raises(PermanentToolError, match="required"):
            await tool.invoke({"task_id": "", "status": "", "reason": ""}, ctx)


# ---------------------------------------------------------------------------
# AskHumanTool
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
        questions = board._human_channel.get_pending_questions(project_id=board.project_id)
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
# CheckMessagesTool
# ---------------------------------------------------------------------------

class TestCheckMessagesTool:
    def test_schema_has_clear_and_batch_size(self):
        tool = CheckMessagesTool()
        schema = tool.schema()
        assert "clear" in schema["properties"]
        assert "batch_size" in schema["properties"]

    @pytest.mark.asyncio
    async def test_invoke_returns_messages(self):
        board = StateBoard("obj")
        board.register_agent("reviewer-1", "reviewer")

        # Send a message via the mailbox
        from openagents_orchestration.models.message import StructuredMessage
        msg = StructuredMessage.from_text("coder-1", "reviewer-1", "Please check line 42")
        await board.send_structured(msg)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="reviewer-1")
        tool = CheckMessagesTool()
        result = await tool.invoke({}, ctx)

        assert result["count"] == 1
        assert "line 42" in result["message"]

    @pytest.mark.asyncio
    async def test_invoke_no_messages(self):
        board = StateBoard("obj")
        board.register_agent("reviewer-1", "reviewer")

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="reviewer-1")
        tool = CheckMessagesTool()
        result = await tool.invoke({}, ctx)

        assert result["count"] == 0
        assert "No new messages" in result["message"]

    @pytest.mark.asyncio
    async def test_invoke_no_board_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="reviewer-1")
        tool = CheckMessagesTool()
        with pytest.raises(PermanentToolError, match="StateBoard"):
            await tool.invoke({}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_with_clear_false(self):
        board = StateBoard("obj")
        board.register_agent("reviewer-1", "reviewer")

        from openagents_orchestration.models.message import StructuredMessage
        msg = StructuredMessage.from_text("coder-1", "reviewer-1", "test message")
        await board.send_structured(msg)

        ctx = MockContext(deps=MockContext(state_board=board), agent_id="reviewer-1")
        tool = CheckMessagesTool()
        result = await tool.invoke({"clear": False}, ctx)

        assert result["count"] == 1
        # Message should still be in mailbox since clear=False
        remaining = await board.peek_mailbox("reviewer-1")
        assert len(remaining) == 1
