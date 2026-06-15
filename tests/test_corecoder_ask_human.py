"""Tests for CoreCoder's ask_human tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from openagents.errors.exceptions import PermanentToolError

from openagents_orchestration.tools.corecoder.ask_human import CoreCoderAskHumanTool


def _make_context(state: dict | None = None, board: Any | None = None, agent_id: str = "coder-test") -> MagicMock:
    ctx = MagicMock()
    ctx.agent_id = agent_id
    ctx.state = state if state is not None else {}
    ctx.deps = MagicMock()
    ctx.deps.state_board = board
    return ctx


def test_schema_and_spec():
    tool = CoreCoderAskHumanTool()
    spec = tool.execution_spec()
    assert spec.side_effects == "writes_state"
    assert spec.concurrency_safe is True

    schema = tool.schema()
    assert schema["required"] == ["question"]
    assert "question" in schema["properties"]
    assert "options" in schema["properties"]


@pytest.mark.asyncio
async def test_invoke_without_state_board():
    tool = CoreCoderAskHumanTool()
    ctx = _make_context()
    result = await tool.invoke(
        {"question": "Which module should I focus on?", "options": "patterns, tools, runner"},
        ctx,
    )

    assert "[Awaiting human reply]" in result
    assert "Which module should I focus on?" in result
    assert "patterns, tools, runner" in result
    assert ctx.state["__pending_human_question__"]["question"] == "Which module should I focus on?"
    assert ctx.state["__awaiting_human_reply__"]["qid"] is None


@pytest.mark.asyncio
async def test_invoke_with_state_board():
    tool = CoreCoderAskHumanTool()
    board = MagicMock()
    board.ask_human.return_value = "hq-12345678"
    ctx = _make_context(board=board)

    result = await tool.invoke(
        {"question": "Should I refactor the runner?"},
        ctx,
    )

    board.ask_human.assert_called_once_with(
        "Should I refactor the runner?",
        options="",
        from_agent="coder-test",
    )
    assert "hq-12345678" in result
    assert ctx.state["__awaiting_human_reply__"]["qid"] == "hq-12345678"
    assert ctx.state["__pending_human_question__"]["qid"] == "hq-12345678"


@pytest.mark.asyncio
async def test_invoke_requires_question():
    tool = CoreCoderAskHumanTool()
    ctx = _make_context()
    with pytest.raises(PermanentToolError, match="question is required"):
        await tool.invoke({"question": "   "}, ctx)


@pytest.mark.asyncio
async def test_invoke_requires_context():
    tool = CoreCoderAskHumanTool()
    with pytest.raises(PermanentToolError, match="RunContext required"):
        await tool.invoke({"question": "hello"}, None)


def test_ask_human_not_readonly():
    """ask_human should not be classified as a read-only tool."""
    tool = CoreCoderAskHumanTool()
    spec = tool.execution_spec()
    assert getattr(spec, "reads_files", False) is False
    assert getattr(spec, "writes_files", False) is False
    assert spec.side_effects == "writes_state"
