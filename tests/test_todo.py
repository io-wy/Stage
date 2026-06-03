"""Tests for todo_write / todo_read tools."""

from __future__ import annotations

import asyncio

import pytest

from openagents_orchestration.tools.corecoder.todo import TodoReadTool, TodoWriteTool


class MockContext:
    def __init__(self):
        self.scratch: dict[str, object] = {}


@pytest.fixture
def ctx():
    return MockContext()


class TestTodoWriteTool:
    def test_write_and_read(self, ctx):
        write = TodoWriteTool()
        result = asyncio.run(
            write.invoke(
                {
                    "todos": [
                        {"id": "1", "content": "Implement A", "status": "in_progress"},
                        {"id": "2", "content": "Test A", "status": "pending"},
                    ]
                },
                ctx,
            )
        )
        assert len(result["todos"]) == 2
        assert result["summary"] == "0/2 done, 1 in progress"
        assert len(ctx.scratch["todo_list"]) == 2

    def test_only_one_in_progress(self, ctx):
        write = TodoWriteTool()
        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                write.invoke(
                    {
                        "todos": [
                            {"id": "1", "content": "A", "status": "in_progress"},
                            {"id": "2", "content": "B", "status": "in_progress"},
                        ]
                    },
                    ctx,
                )
            )
        assert "Only one task may be in_progress" in str(exc_info.value)

    def test_invalid_status(self, ctx):
        write = TodoWriteTool()
        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                write.invoke(
                    {"todos": [{"id": "1", "content": "A", "status": "bad"}]},
                    ctx,
                )
            )
        assert "pending/in_progress/completed" in str(exc_info.value)

    def test_update_overwrites(self, ctx):
        write = TodoWriteTool()
        asyncio.run(
            write.invoke(
                {"todos": [{"id": "1", "content": "A", "status": "pending"}]},
                ctx,
            )
        )
        asyncio.run(
            write.invoke(
                {"todos": [{"id": "1", "content": "A done", "status": "completed"}]},
                ctx,
            )
        )
        assert ctx.scratch["todo_list"][0]["status"] == "completed"


class TestTodoReadTool:
    def test_read_empty(self, ctx):
        read = TodoReadTool()
        result = asyncio.run(read.invoke({}, ctx))
        assert result["todos"] == []
        assert "No todos yet" in result["message"]

    def test_read_with_todos(self, ctx):
        ctx.scratch["todo_list"] = [
            {"id": "1", "content": "A", "status": "completed", "priority": "high"},
            {"id": "2", "content": "B", "status": "in_progress", "priority": "medium"},
        ]
        read = TodoReadTool()
        result = asyncio.run(read.invoke({}, ctx))
        assert len(result["todos"]) == 2
        assert result["summary"] == "1/2 done"
        assert "[x]" in result["message"]
        assert "[>]" in result["message"]
