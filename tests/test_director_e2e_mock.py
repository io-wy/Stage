"""Director end-to-end with mock LLM.

Validates the pattern-centric rework: Director ReAct loop drives
classify_intent → decompose → spawn_agent → finalize, and Runner.run()
simply hands control to the Director.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import Budget, TaskStatus
from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.tools.director.classify_intent import ClassifyIntentTool
from openagents_orchestration.tools.director.decompose import DecomposeTool

# ---------------------------------------------------------------------------
# Fake LLM helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "call_1"


@dataclass
class FakeResponse:
    output_text: str = ""
    content: list[dict[str, Any]] | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    usage: Any | None = None


class FakeLLMClient:
    """LLM client that replays a scripted sequence of responses.

    Tool-less calls — the lightweight planning phase routes through
    ``structured_generate`` with ``tools=None`` — are served a fixed,
    high-confidence plan and do NOT consume the scripted ReAct sequence.
    This lets the real planning phase run instead of disabling it, while
    keeping the scripted tool-call turns aligned.
    """

    # Minimal _PlanSchema-valid payload: confident enough to skip the
    # clarification pause; no files/steps required.
    _PLAN_JSON = '{"steps": ["execute the task"], "confidence": 8}'

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        # Planning / structured-output calls are tool-less. Serve a canned
        # plan without advancing the scripted (tool-calling) sequence.
        if kwargs.get("tools") is None:
            return FakeResponse(output_text=self._PLAN_JSON)
        if self._index >= len(self._responses):
            return FakeResponse(output_text="")
        response = self._responses[self._index]
        self._index += 1
        return response


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_react_loop_classify_decompose_spawn_finalize(tmp_path, monkeypatch):
    """End-to-end: Director classifies, decomposes, spawns coder, and finalizes."""
    monkeypatch.chdir(tmp_path)

    # Make verification easy for the coder.
    monkeypatch.setattr(
        "openagents_orchestration.patterns.corecoder._detect_project_type",
        lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
    )

    objective = "Create a hello.py file that prints hello"

    config_path = Path(__file__).parent.parent / "agent.json"
    runner = OrchestratorRunner(config_path)

    # -- Mock classify_intent tool: always return a complex intent ---------------
    async def fake_classify(self, params, context):
        return {
            "intent": {
                "task_type": "feature",
                "complexity": "complex",
                "external": [],
                "priority": "normal",
                "confidence": 0.9,
                "reason": "Needs file creation",
                "source": "mock",
            }
        }

    # -- Mock decompose tool: add a single task to StateBoard --------------------
    async def fake_decompose(self, params, context):
        board = getattr(getattr(context, "deps", None), "state_board", None)
        assert board is not None, "StateBoard not available in decompose tool"
        graph = TaskGraph(
            objective=params.get("objective", objective),
            tasks=[
                TaskNode(
                    task_id="t1",
                    description="Create hello.py",
                    agent_type="coder",
                    input_context="Write a file hello.py that prints 'hello' when run.",
                    expected_artifacts=["hello.py"],
                ),
            ],
        )
        board.add_tasks(graph)
        return {"tasks_added": 1, "task_ids": ["t1"]}

    # -- Scripted LLM responses for Director ------------------------------------
    director_llm = FakeLLMClient(
        [
            FakeResponse(tool_calls=[FakeToolCall("classify_intent", {"objective": objective})]),
            FakeResponse(tool_calls=[FakeToolCall("decompose", {"objective": objective})]),
            FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
            FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t1"})]),
            FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
            FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "Created hello.py"})]),
            FakeResponse(output_text="Done"),
        ]
    )

    # -- Scripted LLM responses for coder ---------------------------------------
    coder_llm = FakeLLMClient(
        [
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "write_file",
                        {"file_path": "hello.py", "content": "print('hello')\n"},
                    )
                ]
            ),
            FakeResponse(tool_calls=[FakeToolCall("bash", {"command": "python hello.py"})]),
            FakeResponse(output_text="Completed.\n\nFILES_CREATED: hello.py"),
        ]
    )

    original_ensure_bundle = OrchestratorRunner._ensure_bundle

    def patched_ensure_bundle(self, agent_id: str):
        bundle = original_ensure_bundle(self, agent_id)
        if agent_id == "director":
            bundle.llm_client = director_llm
        elif agent_id == "coder":
            bundle.llm_client = coder_llm
        return bundle

    with (
        patch.object(OrchestratorRunner, "_ensure_bundle", patched_ensure_bundle),
        patch.object(ClassifyIntentTool, "invoke", fake_classify),
        patch.object(DecomposeTool, "invoke", fake_decompose),
    ):
        report = await runner.run(
            objective,
            budget=Budget(token_limit=10_000, time_limit_s=300, max_steps=50),
            work_dir=str(tmp_path),
        )

    board = runner.state_board
    assert board is not None

    task = board.get_task("t1")
    assert task is not None
    assert task.status == TaskStatus.COMPLETED, f"task status: {task.status}"
    # artifact 辅助层已从主链路剥离（见 hooks/state_sync.py）——board.artifacts 不再
    # 由记账员填充，故下面两条核验断言暂时停用。文件真伪改由磁盘断言（下方 written）守护。
    # assert "hello.py" in board.artifacts
    # assert board.artifacts["hello.py"].status == "verified"
    assert board._final_summary

    written = tmp_path / "hello.py"
    assert written.exists()
    assert written.read_text() == "print('hello')\n"

    # Decision history should record at least the spawn → completed decision
    history_summary = board.decision_history.summary()
    assert history_summary["total_decisions"] >= 1

    # Report should reflect success
    assert report is not None
    assert "verification_report" in report.metadata
