"""Tests for CoreCoder planning-phase uncertainty assessment."""

from __future__ import annotations

import json
from typing import Any

import pytest
from openagents.interfaces.run_context import RunContext, RunUsage
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.patterns.corecoder import CoreCoderPattern


class FakeLLMClient:
    """Minimal fake LLM client for testing CoreCoderPattern."""

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
        content: list[dict[str, Any]] | None = None,
        tool_calls: list[Any] | None = None,
        usage: Any = None,
    ):
        self.output_text = output_text
        self.content = content or []
        self.tool_calls = tool_calls or []
        self.usage = usage


class FakeEventBus:
    async def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


class NoOpTool(ToolPlugin):
    name = "no_op"
    description = "Does nothing"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": "ok"}


class AskHumanTool(ToolPlugin):
    name = "ask_human"
    description = "Ask human"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="writes_state")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "string"},
            },
            "required": ["question"],
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        context.state["__ask_human_invoked__"] = {
            "question": params.get("question"),
            "options": params.get("options"),
        }
        context.state["__pending_human_question__"] = {
            "qid": None,
            "question": params.get("question"),
            "options": params.get("options", ""),
            "from_agent": getattr(context, "agent_id", "unknown"),
        }
        context.state["__awaiting_human_reply__"] = {
            "qid": None,
            "question": params.get("question"),
            "options": params.get("options", ""),
            "from_agent": getattr(context, "agent_id", "unknown"),
        }
        return f"[Awaiting human reply] {params.get('question')}"


def _make_run_context(
    llm_client: FakeLLMClient,
    tools: dict[str, ToolPlugin] | None = None,
    input_text: str = "Do something",
) -> RunContext[Any]:
    return RunContext(
        agent_id="test-agent",
        session_id="test-session",
        run_id="run-1",
        input_text=input_text,
        llm_client=llm_client,
        tools=tools or {},
        event_bus=FakeEventBus(),
        state={},
        scratch={},
        transcript=[],
        system_prompt_fragments=[],
        usage=RunUsage(),
        tool_results=[],
    )


async def _setup_pattern(pattern: CoreCoderPattern, ctx: RunContext[Any]) -> None:
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


@pytest.mark.asyncio
async def test_planning_with_clarification_pauses():
    plan_json = json.dumps(
        {
            "files_to_read": [],
            "files_to_edit": [],
            "tests_to_run": [],
            "steps": ["Ask human for scope"],
            "confidence": 2,
            "clarification_needed": "Which module should I focus on?",
        }
    )
    llm = FakeLLMClient([FakeResponse(output_text=plan_json)])
    pattern = CoreCoderPattern(config={"enable_planning": True})
    ctx = _make_run_context(
        llm,
        tools={"ask_human": AskHumanTool(), "no_op": NoOpTool()},
        input_text="看看 Stage 有什么问题",
    )
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert "[Awaiting human reply]" in result
    assert "Which module should I focus on?" in result
    assert ctx.state["__awaiting_human_reply__"]["question"] == "Which module should I focus on?"
    assert ctx.state["__clarification_question__"] == "Which module should I focus on?"
    assert ctx.state["__ask_human_invoked__"]["question"] == "Which module should I focus on?"


@pytest.mark.asyncio
async def test_planning_with_high_confidence_runs():
    plan_json = json.dumps(
        {
            "files_to_read": ["src/foo.py"],
            "files_to_edit": ["src/foo.py"],
            "tests_to_run": ["pytest"],
            "steps": ["Read foo", "Edit foo", "Run tests"],
            "confidence": 8,
            "clarification_needed": None,
        }
    )
    llm = FakeLLMClient(
        [
            FakeResponse(output_text=plan_json),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern(config={"enable_planning": True})
    ctx = _make_run_context(llm)
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result == "Done"
    assert "__awaiting_human_reply__" not in ctx.state
    plan = ctx.state.get("__plan__")
    assert plan["confidence"] == 8


@pytest.mark.asyncio
async def test_clarification_disabled_ignores_low_confidence():
    plan_json = json.dumps(
        {
            "files_to_read": ["src/foo.py"],
            "files_to_edit": ["src/foo.py"],
            "tests_to_run": ["pytest"],
            "steps": ["Read foo", "Edit foo", "Run tests"],
            "confidence": 2,
            "clarification_needed": "What should I do?",
        }
    )
    llm = FakeLLMClient(
        [
            FakeResponse(output_text=plan_json),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern(
        config={"enable_planning": True, "enable_clarification": False}
    )
    ctx = _make_run_context(llm)
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result == "Done"
    assert "__awaiting_human_reply__" not in ctx.state


@pytest.mark.asyncio
async def test_clarification_threshold_respected():
    plan_json = json.dumps(
        {
            "files_to_read": [],
            "files_to_edit": [],
            "tests_to_run": [],
            "steps": [],
            "confidence": 5,
            "clarification_needed": "What is the priority?",
        }
    )
    # Default threshold is 4, so confidence=5 should NOT trigger clarification.
    llm = FakeLLMClient(
        [
            FakeResponse(output_text=plan_json),
            FakeResponse(output_text="Done"),
        ]
    )
    pattern = CoreCoderPattern(config={"enable_planning": True})
    ctx = _make_run_context(
        llm,
        tools={"ask_human": AskHumanTool(), "no_op": NoOpTool()},
    )
    await _setup_pattern(pattern, ctx)

    result = await pattern.execute()

    assert result == "Done"
    assert "__awaiting_human_reply__" not in ctx.state


def test_clarification_config_defaults():
    pattern = CoreCoderPattern(config={})
    assert pattern._enable_clarification is True
    assert pattern._clarification_confidence_threshold == 4


def test_clarification_config_overrides():
    pattern = CoreCoderPattern(
        config={
            "enable_clarification": False,
            "clarification_confidence_threshold": 7,
        }
    )
    assert pattern._enable_clarification is False
    assert pattern._clarification_confidence_threshold == 7
