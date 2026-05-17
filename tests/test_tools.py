"""Tests for orchestrator tools."""

from __future__ import annotations

import asyncio

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.state_board import StateBoard
from openagents_orchestration.tools.ask_human import AskHumanTool
from openagents_orchestration.tools.finalize import FinalizeTool
from openagents_orchestration.tools.replan import ReplanTool
from openagents_orchestration.tools.send_message import SendMessageTool
from openagents_orchestration.tools.check_messages import CheckMessagesTool
from openagents_orchestration.tools.show_state import ShowStateTool
from openagents_orchestration.tools.spawn_agent import SpawnAgentTool


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestShowStateTool:
    def test_schema_and_spec(self):
        tool = ShowStateTool()
        spec = tool.execution_spec()
        assert spec.concurrency_safe is True
        schema = tool.schema()
        assert "section" in schema.get("properties", {})

    def test_invoke(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(objective="obj", tasks=[TaskNode("t1", "task", "coder")]))
        ctx = MockContext(deps=MockContext(state_board=board))

        tool = ShowStateTool()
        result = asyncio.run(tool.invoke({}, ctx))
        assert "obj" in result
        assert "t1" in result


class TestFinalizeTool:
    def test_invoke(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board))

        tool = FinalizeTool()
        result = asyncio.run(tool.invoke({"summary": "All done"}, ctx))
        assert result == "All done"
        assert board._final_summary == "All done"

    def test_invoke_missing_summary(self):
        tool = FinalizeTool()
        ctx = MockContext(deps=MockContext(state_board=StateBoard("obj")))
        with pytest.raises(Exception):
            asyncio.run(tool.invoke({}, ctx))


class TestSendMessageTool:
    def test_invoke(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="director")

        tool = SendMessageTool()
        result = asyncio.run(tool.invoke({"to_agent": "coder", "message": "hello"}, ctx))
        assert "coder" in result
        assert len(board._pending_messages) == 1


class TestSpawnAgentTool:
    def test_schema(self):
        tool = SpawnAgentTool()
        schema = tool.schema()
        assert "task_id" in schema.get("properties", {})

    def test_build_input(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder", input_context="write hello.py"),
            ],
        ))
        task = board.get_task("t1")
        inp = SpawnAgentTool._build_input(task, board)
        assert "write hello.py" in inp

    def test_extract_artifacts(self):
        output = "FILES_CREATED: hello.py, test.py\nSUMMARY: done"
        arts = SpawnAgentTool._extract_artifacts(output)
        assert "hello.py" in arts
        assert "test.py" in arts


class TestReplanTool:
    def test_schema(self):
        tool = ReplanTool()
        schema = tool.schema()
        assert "task_id" in schema.get("properties", {})
        assert "reason" in schema.get("properties", {})


class TestAskHumanTool:
    def test_invoke(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder")

        tool = AskHumanTool()
        result = asyncio.run(tool.invoke({"question": "Which auth?"}, ctx))
        assert "Which auth?" in result
        assert len(board._human_questions) == 1


class TestCheckMessagesTool:
    def test_no_messages(self):
        board = StateBoard("obj")
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder-1")

        tool = CheckMessagesTool()
        result = asyncio.run(tool.invoke({}, ctx))
        assert result["count"] == 0
        assert "No new messages" in result["message"]

    def test_with_messages(self):
        board = StateBoard("obj")
        board._pending_messages = [
            {"from": "director", "to": "coder-1", "content": "fix line 42"},
            {"from": "reviewer", "to": "other", "content": "wrong"},
        ]
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="coder-1")

        tool = CheckMessagesTool()
        result = asyncio.run(tool.invoke({}, ctx))
        assert result["count"] == 1
        assert "fix line 42" in result["message"]
        # Message should be cleared
        assert len(board._pending_messages) == 1
        assert board._pending_messages[0]["to"] == "other"

    def test_no_clear(self):
        board = StateBoard("obj")
        board._pending_messages = [
            {"from": "director", "to": "*", "content": "broadcast"},
        ]
        ctx = MockContext(deps=MockContext(state_board=board), agent_id="any")

        tool = CheckMessagesTool()
        result = asyncio.run(tool.invoke({"clear": False}, ctx))
        assert result["count"] == 1
        assert len(board._pending_messages) == 1
