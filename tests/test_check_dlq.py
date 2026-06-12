"""Tests for CheckDLQTool."""

from __future__ import annotations

import asyncio

import pytest

from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.tools.monitor.check_dlq import CheckDLQTool


class MockDeps:
    def __init__(self, board):
        self.state_board = board


class MockContext:
    def __init__(self, deps):
        self.deps = deps
        self.agent_id = "director"


@pytest.mark.asyncio
async def test_check_dlq_empty():
    board = StateBoard("obj", echo=False)
    tool = CheckDLQTool()
    result = await tool.invoke({}, MockContext(deps=MockDeps(board)))

    assert result["count"] == 0
    assert "No dead-letter" in result["message"]


@pytest.mark.asyncio
async def test_check_dlq_finds_dead_letters():
    board = StateBoard("obj", echo=False)
    board.register_agent("coder-1", "coder")

    # Push a message and nack it 3 times to move it to DLQ
    msg = StructuredMessage.from_text("director", "coder-1", "do work")
    await board.send_structured(msg)
    for i in range(3):
        claimed = await board.claim_messages("coder-1")
        if claimed:
            await board.nack_message("coder-1", claimed[0].msg_id, "failed")
        if i < 2:
            await asyncio.sleep(2 ** i)  # backoff: 1s, 2s

    tool = CheckDLQTool()
    result = await tool.invoke({}, MockContext(deps=MockDeps(board)))

    assert result["count"] == 1
    assert "coder-1" in result["agents"]
    assert result["agents"]["coder-1"]["size"] == 1
    assert "failed" in result["message"]


@pytest.mark.asyncio
async def test_check_dlq_limit():
    board = StateBoard("obj", echo=False)
    board.register_agent("coder-1", "coder")

    for i in range(3):
        msg = StructuredMessage.from_text("director", "coder-1", f"work {i}")
        await board.send_structured(msg)
        # Nack three times to move this message into DLQ
        for j in range(3):
            claimed = await board.claim_messages("coder-1")
            if claimed:
                await board.nack_message("coder-1", claimed[0].msg_id, "failed")
            if j < 2:
                await asyncio.sleep(2 ** j)  # backoff: 1s, 2s

    tool = CheckDLQTool()
    result = await tool.invoke({"limit": 2}, MockContext(deps=MockDeps(board)))

    assert result["count"] == 3
    assert len(result["agents"]["coder-1"]["latest"]) == 2
