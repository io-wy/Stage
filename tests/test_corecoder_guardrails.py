"""Tests for CoreCoder deterministic guardrails (step budget / exploration cap)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openagents_orchestration.patterns.corecoder import CoreCoderPattern


def _make_tool(name: str, side_effects: str, writes_files: bool = False) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool._tool = tool
    spec = MagicMock()
    spec.side_effects = side_effects
    spec.writes_files = writes_files
    tool.execution_spec = lambda: spec
    return tool


class _AsyncEventBus:
    async def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


class _FakeToolCall:
    def __init__(self, name: str):
        self.name = name
        self.arguments = {}
        self.id = "call_1"
        self.raw_arguments = None
        self.type = "tool_call"


@pytest.mark.asyncio
async def test_is_readonly_tool_call_detects_readonly_tools() -> None:
    pattern = CoreCoderPattern(config={})
    pattern.context = MagicMock()
    pattern.context.tools = {
        "read_file": _make_tool("read_file", "readonly", writes_files=False),
        "list_directory": _make_tool("list_directory", "readonly"),
        "edit_file": _make_tool("edit_file", "write", writes_files=True),
        "bash": _make_tool("bash", "external"),
        "think": _make_tool("think", "none"),
    }

    assert pattern._is_readonly_tool_call(_FakeToolCall("read_file")) is True
    assert pattern._is_readonly_tool_call(_FakeToolCall("list_directory")) is True
    assert pattern._is_readonly_tool_call(_FakeToolCall("think")) is True
    assert pattern._is_readonly_tool_call(_FakeToolCall("edit_file")) is False
    assert pattern._is_readonly_tool_call(_FakeToolCall("bash")) is False
    assert pattern._is_readonly_tool_call(_FakeToolCall("unknown")) is False


def test_build_readonly_budget_message_triggers_after_threshold() -> None:
    pattern = CoreCoderPattern(config={"max_consecutive_readonly_steps": 3})
    msg = pattern._build_readonly_budget_message(3, 10)
    assert msg is not None
    assert "3 consecutive exploration-only steps" in msg
    assert "concrete action" in msg

    assert pattern._build_readonly_budget_message(0, 10) is None
    assert pattern._build_readonly_budget_message(2, 10) is None


def test_build_step_budget_warning_triggers_near_limit() -> None:
    pattern = CoreCoderPattern(config={"step_budget_warning_steps": 5})
    assert pattern._build_step_budget_warning(25, 30) is not None
    assert "5 step(s) remain" in pattern._build_step_budget_warning(25, 30)
    assert pattern._build_step_budget_warning(20, 30) is None
    assert pattern._build_step_budget_warning(30, 30) is None


def test_config_defaults() -> None:
    pattern = CoreCoderPattern(config={})
    assert pattern._max_consecutive_readonly_steps == 5
    assert pattern._step_budget_warning_steps == 5


def test_config_overrides() -> None:
    pattern = CoreCoderPattern(
        config={
            "max_consecutive_readonly_steps": 2,
            "step_budget_warning_steps": 3,
        }
    )
    assert pattern._max_consecutive_readonly_steps == 2
    assert pattern._step_budget_warning_steps == 3


def test_ask_human_not_treated_as_readonly() -> None:
    pattern = CoreCoderPattern(config={})
    pattern.context = MagicMock()
    pattern.context.tools = {
        "ask_human": _make_tool("ask_human", "writes_state"),
    }
    assert pattern._is_readonly_tool_call(_FakeToolCall("ask_human")) is False


def test_readonly_budget_message_mentions_ask_human() -> None:
    pattern = CoreCoderPattern(config={"max_consecutive_readonly_steps": 3})
    msg = pattern._build_readonly_budget_message(3, 10)
    assert msg is not None
    assert "ask_human" in msg


def test_tool_gating_config_defaults() -> None:
    pattern = CoreCoderPattern(config={})
    assert pattern._enable_tool_gating is True
    assert pattern._tool_gating_threshold == 3


def test_tool_gating_config_overrides() -> None:
    pattern = CoreCoderPattern(
        config={"enable_tool_gating": False, "tool_gating_threshold": 2}
    )
    assert pattern._enable_tool_gating is False
    assert pattern._tool_gating_threshold == 2


def test_is_exploration_tool_id_detects_read_search_think() -> None:
    pattern = CoreCoderPattern(config={})
    pattern.context = MagicMock()
    pattern.context.tools = {
        "read_file": _make_tool("read_file", "readonly"),
        "list_directory": _make_tool("list_directory", "readonly"),
        "glob": _make_tool("glob", "readonly"),
        "grep": _make_tool("grep", "readonly"),
        "web_search": _make_tool("web_search", "readonly"),
        "web_fetch": _make_tool("web_fetch", "readonly"),
        "think": _make_tool("think", "none"),
        "edit_file": _make_tool("edit_file", "write", writes_files=True),
        "bash": _make_tool("bash", "external"),
        "ask_human": _make_tool("ask_human", "writes_state"),
        "complete_task": _make_tool("complete_task", "writes_state"),
    }

    assert pattern._is_exploration_tool_id("read_file") is True
    assert pattern._is_exploration_tool_id("list_directory") is True
    assert pattern._is_exploration_tool_id("glob") is True
    assert pattern._is_exploration_tool_id("grep") is True
    assert pattern._is_exploration_tool_id("web_search") is True
    assert pattern._is_exploration_tool_id("web_fetch") is True
    assert pattern._is_exploration_tool_id("think") is True
    assert pattern._is_exploration_tool_id("edit_file") is False
    assert pattern._is_exploration_tool_id("bash") is False
    assert pattern._is_exploration_tool_id("ask_human") is False
    assert pattern._is_exploration_tool_id("complete_task") is False
    assert pattern._is_exploration_tool_id("unknown") is False


def test_build_gated_tool_schemas_removes_exploration_tools() -> None:
    pattern = CoreCoderPattern(config={})
    pattern.context = MagicMock()
    pattern.context.llm_client = MagicMock()
    pattern.context.llm_client.provider_name = "anthropic"
    pattern.context.tools = {
        "read_file": _make_tool("read_file", "readonly"),
        "edit_file": _make_tool("edit_file", "write", writes_files=True),
        "bash": _make_tool("bash", "external"),
        "ask_human": _make_tool("ask_human", "writes_state"),
    }

    gated = pattern._build_gated_tool_schemas()
    names = {s["name"] for s in gated}

    assert "read_file" not in names
    assert "edit_file" in names
    assert "bash" in names
    assert "ask_human" in names


def test_build_tool_gate_message_triggers_after_threshold() -> None:
    pattern = CoreCoderPattern(config={"tool_gating_threshold": 3})
    msg = pattern._build_tool_gate_message(3, 10)
    assert msg is not None
    assert "EXPLORATION BUDGET EXHAUSTED" in msg
    assert "read/search tools are now disabled" in msg.lower()

    assert pattern._build_tool_gate_message(0, 10) is None
    assert pattern._build_tool_gate_message(2, 10) is None


def test_build_tool_gate_message_disabled_when_gating_off() -> None:
    pattern = CoreCoderPattern(
        config={"enable_tool_gating": False, "tool_gating_threshold": 3}
    )
    assert pattern._build_tool_gate_message(5, 10) is None


@pytest.mark.asyncio
async def test_gated_exploration_tool_call_rejected() -> None:
    """When gated, exploration tool calls are rejected without executing."""
    from openagents.interfaces.run_context import RunContext, RunUsage

    pattern = CoreCoderPattern(
        config={"enable_planning": False, "tool_gating_threshold": 1}
    )
    ctx = RunContext(
        agent_id="test-agent",
        session_id="test-session",
        run_id="run-1",
        input_text="Do something",
        llm_client=None,
        tools={
            "read_file": _make_tool("read_file", "readonly"),
            "edit_file": _make_tool("edit_file", "write", writes_files=True),
        },
        event_bus=_AsyncEventBus(),
        state={"__consecutive_readonly_steps__": 2},
        scratch={},
        transcript=[],
        system_prompt_fragments=[],
        usage=RunUsage(),
        tool_results=[],
    )
    await pattern.setup(
        agent_id=ctx.agent_id,
        session_id=ctx.session_id,
        input_text=ctx.input_text,
        state=ctx.state,
        tools=ctx.tools,
        llm_client=ctx.llm_client,
        llm_options=ctx.llm_options,
        event_bus=ctx.event_bus,
        transcript=ctx.transcript,
        usage=ctx.usage,
        scratch=ctx.scratch,
        tool_results=ctx.tool_results,
        system_prompt_fragments=ctx.system_prompt_fragments,
    )

    desc = MagicMock()
    desc.tool_id = "read_file"
    desc.params = {"file_path": "x.py"}
    desc.call_id = "call_1"
    desc.index = 0

    result = await pattern._dispatch_single_tool(desc, step=1)

    assert result.success is False
    assert "disabled" in result.error.lower()
    ctx.tools["read_file"].invoke.assert_not_called()

