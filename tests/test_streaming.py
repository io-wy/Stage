"""Tests for CoreCoderPattern streaming and tool pre-execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.models.stream import StreamEventType
from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from openagents_orchestration.patterns.stream_parser import PreExecutionCache


class _FakeEventBus:
    async def emit(self, *args, **kwargs):
        pass


async def _setup_pattern(pattern, ctx):
    await pattern.setup(
        agent_id="test-agent",
        session_id="test-session",
        input_text=ctx.input_text,
        state=ctx.state,
        transcript=ctx.transcript,
        scratch=ctx.scratch,
        llm_client=ctx.llm_client,
        llm_options=ctx.llm_options,
        event_bus=ctx.event_bus,
        tools=ctx.tools,
    )


def _make_run_context(input_text: str = "hello", tools: dict | None = None):
    ctx = MagicMock()
    ctx.agent_id = "test-agent"
    ctx.session_id = "test-session"
    ctx.input_text = input_text
    ctx.state = {}
    ctx.scratch = {}
    ctx.transcript = []
    ctx.tool_results = []
    ctx.system_prompt_fragments = []
    ctx.memory_view = {}
    ctx.artifacts = []
    ctx.session_artifacts = []
    ctx.llm_options = {}
    ctx.event_bus = _FakeEventBus()
    ctx.usage = MagicMock()
    ctx.usage.llm_calls = 0
    ctx.usage.input_tokens = 0
    ctx.usage.output_tokens = 0
    ctx.usage.total_tokens = 0
    ctx.usage.input_tokens_cached = 0
    ctx.usage.input_tokens_cache_creation = 0
    ctx.usage.cost_usd = 0.0
    ctx.usage.cost_breakdown = {}
    ctx.usage.tool_calls = 0
    ctx.usage.model_dump.return_value = {}
    ctx.llm_client = AsyncMock()
    ctx.llm_client.provider_name = "openai_compatible"
    ctx.llm_client.generate = AsyncMock()
    ctx.llm_client.complete_stream = None  # force fallback path for tests
    ctx.tools = tools or {}
    ctx.deps = None
    return ctx


class _FakeToolCall:
    def __init__(self, name, args, call_id):
        self.name = name
        self.arguments = args
        self.id = call_id
        self.raw_arguments = None
        self.type = "tool_call"


@pytest.mark.asyncio
async def test_execute_stream_yields_text_and_complete() -> None:
    pattern = CoreCoderPattern(config={"enable_planning": False})
    ctx = _make_run_context()
    response = MagicMock()
    response.content = []
    response.output_text = "final answer"
    response.tool_calls = []
    response.usage = None
    response.stop_reason = "stop"
    ctx.llm_client.generate.return_value = response

    await _setup_pattern(pattern, ctx)

    events = []
    async for event in pattern.execute_stream():
        events.append(event)

    assert events[-1].type == StreamEventType.complete
    assert events[-1].text == "final answer"
    text_events = [e for e in events if e.type == StreamEventType.text]
    assert len(text_events) == 1
    assert text_events[0].text == "final answer"


@pytest.mark.asyncio
async def test_execute_stream_yields_tool_call_events() -> None:
    pattern = CoreCoderPattern(config={"enable_planning": False})
    ctx = _make_run_context()

    response1 = MagicMock()
    response1.content = []
    response1.output_text = ""
    response1.tool_calls = [_FakeToolCall("think", {"thought": "hello"}, "call_1")]
    response1.usage = None
    response1.stop_reason = "tool_calls"

    final_response = MagicMock()
    final_response.content = []
    final_response.output_text = "done thinking"
    final_response.tool_calls = []
    final_response.usage = None
    final_response.stop_reason = "stop"

    ctx.llm_client.generate.side_effect = [response1] + [final_response] * 20

    think_tool = MagicMock()
    think_tool.invoke = AsyncMock(return_value={"thought": "hello", "message": "Thought recorded"})
    think_tool.execution_spec = lambda: MagicMock(concurrency_safe=True)
    ctx.tools = {"think": think_tool}

    await _setup_pattern(pattern, ctx)

    events = []
    async for event in pattern.execute_stream():
        events.append(event)

    start_events = [e for e in events if e.type == StreamEventType.tool_call_start]
    complete_events = [e for e in events if e.type == StreamEventType.tool_call_complete]
    result_events = [e for e in events if e.type == StreamEventType.tool_result]
    assert len(start_events) == 1
    assert len(complete_events) == 1
    assert len(result_events) == 1
    assert complete_events[0].tool_name == "think"
    assert result_events[0].tool_name == "think"


@pytest.mark.asyncio
async def test_execute_stream_does_not_preexecute_write_tools() -> None:
    pattern = CoreCoderPattern(config={"enable_planning": False})
    ctx = _make_run_context()

    response1 = MagicMock()
    response1.content = []
    response1.output_text = ""
    response1.tool_calls = [_FakeToolCall("write_file", {"file_path": "/tmp/x", "content": "hi"}, "call_1")]
    response1.usage = None
    response1.stop_reason = "tool_calls"

    final_response = MagicMock()
    final_response.content = []
    final_response.output_text = "done writing"
    final_response.tool_calls = []
    final_response.usage = None
    final_response.stop_reason = "stop"

    ctx.llm_client.generate.side_effect = [response1] + [final_response] * 20

    write_tool = MagicMock()
    write_tool.invoke = AsyncMock(return_value={"message": "written"})
    write_tool.execution_spec = lambda: MagicMock(concurrency_safe=True)
    ctx.tools = {"write_file": write_tool}

    await _setup_pattern(pattern, ctx)

    events = []
    async for event in pattern.execute_stream():
        events.append(event)

    result_events = [e for e in events if e.type == StreamEventType.tool_result]
    assert len(result_events) == 1
    assert result_events[0].metadata.get("preexecuted") is not True


@pytest.mark.asyncio
async def test_preexecution_cache_hits_and_misses() -> None:
    cache = PreExecutionCache()
    assert cache.get("read_file", {"path": "/tmp/x"}) is None
    cache.set("read_file", {"path": "/tmp/x"}, "content")
    assert cache.get("read_file", {"path": "/tmp/x"}) == "content"
    assert cache.get("read_file", {"path": "/tmp/y"}) is None


@pytest.mark.asyncio
async def test_execute_unchanged_after_streaming_added() -> None:
    """Ensure existing execute() still returns final text."""
    pattern = CoreCoderPattern(config={"enable_planning": False})
    ctx = _make_run_context()
    response = MagicMock()
    response.content = []
    response.output_text = "final answer"
    response.tool_calls = []
    response.usage = None
    response.stop_reason = "stop"
    ctx.llm_client.generate.return_value = response

    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()
    assert result == "final answer"
