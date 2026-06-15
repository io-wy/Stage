"""Tests for think tool."""

from __future__ import annotations

from typing import Any

import pytest
from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext, RunUsage

from openagents_orchestration.tools.corecoder.think import ThinkTool


def _make_context() -> RunContext[Any]:
    return RunContext(
        agent_id="test",
        session_id="test",
        run_id="r1",
        input_text="hi",
        event_bus=object(),
        state={},
        scratch={},
        system_prompt_fragments=[],
        usage=RunUsage(),
        tool_results=[],
    )


@pytest.mark.asyncio
async def test_think_records_thought():
    tool = ThinkTool()
    ctx = _make_context()
    result = await tool.invoke({"thought": "I need to read the config first."}, ctx)

    assert "I need to read the config first" in result["message"]
    assert ctx.scratch["_recent_thoughts"] == ["I need to read the config first."]


@pytest.mark.asyncio
async def test_think_caps_recent_thoughts():
    tool = ThinkTool()
    ctx = _make_context()
    for i in range(7):
        await tool.invoke({"thought": f"thought {i}"}, ctx)

    assert len(ctx.scratch["_recent_thoughts"]) == 5
    assert ctx.scratch["_recent_thoughts"][-1] == "thought 6"


@pytest.mark.asyncio
async def test_think_missing_thought():
    tool = ThinkTool()
    ctx = _make_context()
    with pytest.raises(ToolError, match="thought is required"):
        await tool.invoke({"thought": ""}, ctx)


def test_schema_and_spec():
    tool = ThinkTool()
    spec = tool.execution_spec()
    assert spec.side_effects == "none"
    schema = tool.schema()
    assert "thought" in schema["properties"]
    assert schema["required"] == ["thought"]
