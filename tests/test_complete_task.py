"""Tests for the coder complete_task tool and CoreCoderPattern completion signal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from openagents_orchestration.tools.corecoder.complete_task import CompleteTaskTool


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def emit(self, *args, **kwargs):
        pass


class MockUsage:
    def __init__(self):
        self.llm_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.input_tokens_cached = 0
        self.input_tokens_cache_creation = 0
        self.cost_usd = 0.0
        self.cost_breakdown: dict[str, float] = {}

    def model_dump(self):
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class MockToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "call_1"


@dataclass
class MockLLMResponse:
    content: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[MockToolCall] = field(default_factory=list)
    output_text: str = ""
    usage: Any | None = None


class MockTool:
    def __init__(self, result: Any):
        self._result = result

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        return self._result


class TestCompleteTaskTool:
    def test_schema(self):
        tool = CompleteTaskTool()
        schema = tool.schema()
        assert schema["required"] == ["summary"]
        assert "summary" in schema["properties"]
        assert "artifacts" in schema["properties"]

    def test_invoke_records_summary_and_artifacts(self):
        ctx = MockContext(state={})
        tool = CompleteTaskTool()
        result = asyncio.run(
            tool.invoke(
                {"summary": "Implemented auth", "artifacts": ["src/auth.py"]},
                ctx,
            )
        )
        assert result == "Implemented auth"
        assert ctx.state["__complete_task_summary__"] == "Implemented auth"
        assert ctx.state["__complete_task_artifacts__"] == ["src/auth.py"]

    def test_invoke_allows_no_artifacts(self):
        ctx = MockContext(state={})
        tool = CompleteTaskTool()
        result = asyncio.run(tool.invoke({"summary": "Done"}, ctx))
        assert result == "Done"
        assert "__complete_task_artifacts__" not in ctx.state

    def test_invoke_requires_summary(self):
        from openagents.errors.exceptions import PermanentToolError

        ctx = MockContext(state={})
        tool = CompleteTaskTool()
        with pytest.raises(PermanentToolError):
            asyncio.run(tool.invoke({}, ctx))


class FakeLLMClient:
    provider_name = "openai_compatible"


class FakeEventBus:
    async def emit(self, *args, **kwargs):
        pass


class TestCoreCoderCompletionSignal:
    @pytest.mark.asyncio
    async def test_should_continue_step_true_by_default(self):
        pattern = CoreCoderPattern()
        pattern.context = MockContext(state={})
        assert await pattern._should_continue_step(1) is True

    @pytest.mark.asyncio
    async def test_should_continue_step_false_after_complete_task(self):
        pattern = CoreCoderPattern()
        pattern.context = MockContext(
            state={"__complete_task_summary__": "done"},
        )
        assert await pattern._should_continue_step(1) is False

    @pytest.mark.asyncio
    async def test_execute_stops_on_complete_task_signal(self, monkeypatch):
        """When a tool call sets __complete_task_summary__, execute() returns it."""
        pattern = CoreCoderPattern(config={"max_steps": 10})

        ctx = MockContext(
            agent_id="coder-test",
            session_id="session-test",
            input_text="do it",
            transcript=[],
            state={},
            tools={"complete_task": CompleteTaskTool()},
            usage=MockUsage(),
            scratch={},
            system_prompt_fragments=[],
            llm_client=FakeLLMClient(),
            event_bus=FakeEventBus(),
            tool_results=[],
            artifacts=[],
            session_artifacts=[],
        )
        pattern.context = ctx

        # First LLM turn calls complete_task; second turn sees the signal and stops.
        response_with_complete = MockLLMResponse(
            tool_calls=[MockToolCall("complete_task", {"summary": "All done"})],
            usage=MockUsage(),
        )
        response_stop = MockLLMResponse(output_text="All done")

        calls = [response_with_complete, response_stop]
        call_iter = iter(calls)

        async def fake_invoke_llm(*, messages, tools):
            return next(call_iter)

        monkeypatch.setattr(pattern, "_invoke_llm", fake_invoke_llm)

        final = await pattern.execute()
        assert final == "All done"
        assert ctx.state["__complete_task_summary__"] == "All done"

    @pytest.mark.asyncio
    async def test_execute_sets_step_budget_exhausted_flag(self, monkeypatch):
        """When the LLM keeps calling tools, execute() sets the budget flag."""
        pattern = CoreCoderPattern(config={"max_steps": 2})

        ctx = MockContext(
            agent_id="coder-test",
            session_id="session-test",
            input_text="do it",
            transcript=[],
            state={},
            tools={"noop": MockTool("ok")},
            usage=MockUsage(),
            scratch={},
            system_prompt_fragments=[],
            llm_client=FakeLLMClient(),
            event_bus=FakeEventBus(),
            tool_results=[],
            artifacts=[],
            session_artifacts=[],
        )
        pattern.context = ctx

        async def fake_invoke_llm(*, messages, tools):
            return MockLLMResponse(
                tool_calls=[MockToolCall("noop", {})],
                usage=MockUsage(),
            )

        monkeypatch.setattr(pattern, "_invoke_llm", fake_invoke_llm)

        final = await pattern.execute()
        assert "step budget exhausted" in final
        assert ctx.state["__step_budget_exhausted__"] is True
        assert ctx.state["__steps_used__"] == 2
