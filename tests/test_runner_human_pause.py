"""Tests for runner handling of awaiting-human-reply state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from openagents.interfaces.run_context import RunContext, RunUsage
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import AgentStatus, StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.patterns.corecoder import CoreCoderPattern
from openagents_orchestration.tools.corecoder.ask_human import CoreCoderAskHumanTool


@dataclass
class _FakeRunResult:
    final_output: str = ""
    stop_reason: Any = None
    usage: Any = None
    artifacts: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class FakeStopReason:
    value = "completed"


class FakeUsage:
    total_tokens = 0


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_run_agent_awaiting_human_marks_task_waiting(repo_root: Path):
    runner = OrchestratorRunner(repo_root / "agent.json")
    board = StateBoard("test-obj")
    board.add_tasks(
        TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "看看 Stage 有什么问题", "coder")],
        )
    )
    board.update_task("t1", status=TaskStatus.RUNNING)
    board.register_agent("coder-t1", "coder")
    runner._state_board = board

    fake_result = _FakeRunResult(
        final_output="[Awaiting human reply] Which module should I focus on?",
        stop_reason=FakeStopReason(),
        usage=FakeUsage(),
        metadata={
            "agent_id": "coder-t1",
            "steps_used": 1,
            "tool_calls_used": 0,
            "transcript": [],
            "awaiting_human_reply": {
                "qid": None,
                "question": "Which module should I focus on?",
                "options": "",
                "from_agent": "coder-t1",
            },
        },
    )
    runner._run_single = AsyncMock(return_value=fake_result)  # type: ignore[method-assign]

    output = await runner.run_agent("coder", "看看 Stage 有什么问题", agent_id="coder-t1")

    assert "[Awaiting human reply]" in output
    task = board.get_task("t1")
    assert task is not None
    assert task.status == TaskStatus.WAITING_FOR_HUMAN
    agent = board.agents.get("coder-t1")
    assert agent is not None
    assert agent.status == AgentStatus.WAITING_FOR_HUMAN


@pytest.mark.asyncio
async def test_run_agent_completed_marks_task_completed(repo_root: Path):
    runner = OrchestratorRunner(repo_root / "agent.json")
    board = StateBoard("test-obj")
    board.add_tasks(
        TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "say hello", "coder")],
        )
    )
    board.update_task("t1", status=TaskStatus.RUNNING)
    board.register_agent("coder-t1", "coder")
    runner._state_board = board

    fake_result = _FakeRunResult(
        final_output="Done",
        stop_reason=FakeStopReason(),
        usage=FakeUsage(),
        metadata={
            "agent_id": "coder-t1",
            "steps_used": 1,
            "tool_calls_used": 0,
            "transcript": [],
        },
    )
    runner._run_single = AsyncMock(return_value=fake_result)  # type: ignore[method-assign]

    await runner.run_agent("coder", "say hello", agent_id="coder-t1")

    task = board.get_task("t1")
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    agent = board.agents.get("coder-t1")
    assert agent is not None
    assert agent.status != AgentStatus.WAITING_FOR_HUMAN


@pytest.mark.asyncio
async def test_run_single_includes_awaiting_human_in_metadata(repo_root: Path):
    """Integration-style test: _run_single records __awaiting_human_reply__ in metadata."""

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
        def __init__(self, *, output_text: str = ""):
            self.output_text = output_text
            self.content = []
            self.tool_calls = []
            self.usage = None

    class FakeEventBus:
        async def emit(self, *args: Any, **kwargs: Any) -> None:
            pass

    plan_json = (
        '{"files_to_read": [], "files_to_edit": [], "tests_to_run": [], '
        '"steps": [], "confidence": 2, "clarification_needed": "Which module?"}'
    )
    llm = FakeLLMClient([FakeResponse(output_text=plan_json)])

    pattern = CoreCoderPattern(config={"enable_planning": True})
    ctx = RunContext(
        agent_id="coder-t1",
        session_id="test-session",
        run_id="run-1",
        input_text="看看 Stage 有什么问题",
        llm_client=llm,
        tools={"ask_human": CoreCoderAskHumanTool(), "no_op": _NoOpTool()},
        event_bus=FakeEventBus(),
        state={},
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

    result = await pattern.execute()

    assert "[Awaiting human reply]" in result
    assert ctx.state["__awaiting_human_reply__"]["question"] == "Which module?"


class _NoOpTool(ToolPlugin):
    name = "no_op"
    description = "Does nothing"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="readonly")

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def invoke(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"message": "ok"}
