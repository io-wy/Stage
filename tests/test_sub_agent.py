"""Tests for sub_agent tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from openagents.errors.exceptions import ToolError

from openagents_orchestration.tools.corecoder.sub_agent import SubAgentTool


class FakeStopReason:
    value = "completed"


@dataclass
class FakeResult:
    final_output: str = "done"
    stop_reason: Any = None
    usage: Any = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.stop_reason is None:
            self.stop_reason = FakeStopReason()


async def _async_return(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_sub_agent_via_runner():
    tool = SubAgentTool()
    fake_runner = MagicMock()
    fake_runner.run_agent = MagicMock(return_value=_async_return("sub result"))

    fake_context = MagicMock()
    fake_context.deps.runner = fake_runner

    result = await tool.invoke(
        {"agent_type": "researcher", "instruction": "find examples"},
        fake_context,
    )

    assert result["status"] == "completed"
    assert result["output"] == "sub result"
    fake_runner.run_agent.assert_called_once()
    call_kwargs = fake_runner.run_agent.call_args[1]
    assert call_kwargs["agent_type"] == "researcher"
    assert "find examples" in call_kwargs["input_text"]


@pytest.mark.asyncio
async def test_sub_agent_standalone(tmp_path: Path):
    tool = SubAgentTool()
    fake_context = MagicMock()
    fake_context.deps = None

    fake_result = FakeResult(final_output="standalone result")
    fake_runner = MagicMock()
    fake_runner._run_single = MagicMock(return_value=_async_return(fake_result))
    fake_runner.close = MagicMock(return_value=_async_return(None))

    with patch(
        "openagents_orchestration.tools.corecoder.sub_agent.OrchestratorRunner",
        return_value=fake_runner,
    ), patch.object(Path, "exists", return_value=True):
        result = await tool.invoke(
            {"agent_type": "reviewer", "instruction": "do something"},
            fake_context,
        )

    assert result["status"] == "completed"
    assert result["output"] == "standalone result"


@pytest.mark.asyncio
async def test_sub_agent_depth_limit():
    tool = SubAgentTool()
    fake_context = MagicMock()
    fake_context.state = {"__sub_agent_depth__": 2}
    with pytest.raises(ToolError, match="depth limit"):
        await tool.invoke(
            {"agent_type": "reviewer", "instruction": "x"},
            fake_context,
        )


@pytest.mark.asyncio
async def test_sub_agent_missing_instruction():
    tool = SubAgentTool()
    with pytest.raises(ToolError, match="instruction is required"):
        await tool.invoke({"agent_type": "reviewer", "instruction": ""}, None)


def test_schema_and_spec():
    tool = SubAgentTool()
    spec = tool.execution_spec()
    assert spec.side_effects == "external"
    schema = tool.schema()
    assert "agent_type" in schema["properties"]
    assert "instruction" in schema["properties"]
    assert schema["required"] == ["agent_type", "instruction"]
    assert "enum" not in schema["properties"]["agent_type"]
