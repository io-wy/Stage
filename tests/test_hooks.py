"""Tests for the minimal hook pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from openagents_orchestration.hooks import HookManager
from openagents_orchestration.patterns.corecoder import CoreCoderPattern


class FakeLLMClient:
    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if self._index >= len(self._responses):
            raise RuntimeError("No more fake responses")
        response = self._responses[self._index]
        self._index += 1
        return response


class FakeResponse:
    def __init__(
        self,
        *,
        output_text: str = "",
        tool_calls: list[Any] | None = None,
    ):
        self.output_text = output_text
        self.content = []
        self.tool_calls = tool_calls or []
        self.usage = None


class FakeToolCall:
    def __init__(self, name: str, arguments: dict[str, Any]):
        self.name = name
        self.arguments = arguments
        self.id = "call_1"


class NoOpTool:
    name = "no_op"
    description = "no op"

    def execution_spec(self) -> Any:
        from openagents.interfaces.tool import ToolExecutionSpec

        return ToolExecutionSpec(concurrency_safe=True, side_effects="none")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": "ok"}


class FakeEventBus:
    async def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


def _make_context(llm_client: FakeLLMClient, tools: dict[str, Any], hooks: Any = None):
    from openagents.interfaces.run_context import RunContext, RunUsage

    ctx = RunContext(
        agent_id="test-agent",
        session_id="test-session",
        run_id="run-1",
        input_text="Do something",
        llm_client=llm_client,
        tools=tools,
        event_bus=FakeEventBus(),
        state={},
        scratch={},
        transcript=[],
        system_prompt_fragments=[],
        usage=RunUsage(),
        tool_results=[],
    )
    ctx.deps = type("Deps", (), {"hooks": hooks})()
    return ctx


@pytest.mark.asyncio
async def test_tool_before_invoke_hook_can_block():
    manager = HookManager()
    blocked = {"value": False}

    def blocker(payload: dict[str, Any]) -> dict[str, Any]:
        if payload["tool_id"] == "no_op":
            blocked["value"] = True
            return {"blocked": True, "reason": "test block"}
        return payload

    manager.register("tool.before_invoke", blocker)

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["call no_op"]}'),
            FakeResponse(tool_calls=[FakeToolCall("no_op", {})]),
            FakeResponse(output_text="done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_context(llm, {"no_op": NoOpTool()}, hooks=manager)
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
    # Inject deps after setup so hooks are reachable.
    pattern.context.deps = ctx.deps

    await pattern.execute()
    assert blocked["value"] is True
    assert "Hook blocked" in str(ctx.transcript)


@pytest.mark.asyncio
async def test_pattern_before_llm_hook_can_modify_messages():
    manager = HookManager()
    seen: list[int] = []

    def counter(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(len(payload["messages"]))
        return payload

    manager.register("pattern.before_llm", counter)

    llm = FakeLLMClient(
        [
            FakeResponse(output_text='{"steps": ["finish"]}'),
            FakeResponse(output_text="done"),
        ]
    )
    pattern = CoreCoderPattern()
    ctx = _make_context(llm, {}, hooks=manager)
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
    pattern.context.deps = ctx.deps

    await pattern.execute()
    assert len(seen) >= 1
