"""Golden-path orchestration test.

Drives a full Director → coder → artifact delivery loop using fake LLMs.
No runner_delegate mocking — the real OrchestratorRunner is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openagents_orchestration.core.runner import OrchestratorRunner, RunnerDeps
from openagents_orchestration.core.state_board import Budget, StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.store.artifact_store import LocalArtifactStore


# ---------------------------------------------------------------------------
# Minimal fake LLM helpers (mirrors test_corecoder_enhanced.py)
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
    """LLM client that replays a scripted sequence of responses."""

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if self._index >= len(self._responses):
            # Default fallback: return empty text to end the loop
            return FakeResponse(output_text="")
        response = self._responses[self._index]
        self._index += 1
        return response


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_golden_path_director_spawns_coder_to_create_file(tmp_path, monkeypatch):
    """End-to-end: Director sees a pending task, spawns a coder, coder writes
    and verifies a file, Director finalizes.
    """
    monkeypatch.chdir(tmp_path)

    # Make verification easy: `python hello.py` counts as the test command.
    monkeypatch.setattr(
        "openagents_orchestration.patterns.corecoder._detect_project_type",
        lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
    )

    objective = "Create a hello.py file that prints hello"

    # Pre-build the StateBoard so we bypass intent classification and LLM
    # task decomposition. The only LLM calls in this test are Director and coder.
    board = StateBoard(
        objective,
        budget=Budget(token_limit=10_000, time_limit_s=300, max_steps=50),
    )
    board.add_tasks(
        TaskGraph(
            objective=objective,
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
    )

    config_path = Path(__file__).parent.parent / "agent.json"
    runner = OrchestratorRunner(
        config_path,
        enable_monitor_resident=False,
        collaborative_mode="off",
    )
    runner._current_work_dir = tmp_path
    runner._state_board = board
    runner._deps = RunnerDeps(
        state_board=board,
        runner_delegate=runner.run_agent,
        runner=runner,
        artifact_store=LocalArtifactStore(tmp_path / ".artifacts"),
        matrix_transport=None,
    )

    # Scripted LLM responses for Director
    director_llm = FakeLLMClient(
        [
            FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
            FakeResponse(tool_calls=[FakeToolCall("spawn_agent", {"task_id": "t1"})]),
            FakeResponse(tool_calls=[FakeToolCall("show_state", {})]),
            FakeResponse(tool_calls=[FakeToolCall("finalize", {"summary": "Created hello.py"})]),
        ]
    )

    # Scripted LLM responses for coder
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
            FakeResponse(
                tool_calls=[FakeToolCall("bash", {"command": "python hello.py"})]
            ),
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
        # Disable lightweight planning so scripted tool responses are not
        # consumed by the plan-generation call.
        bundle.plugins.pattern._enable_planning = False
        return bundle

    with patch.object(OrchestratorRunner, "_ensure_bundle", patched_ensure_bundle):
        with patch.object(runner, "_spawn_monitor_resident", return_value=None):
            await runner._run_director_mode(objective)

    # Assertions
    task = board.get_task("t1")
    assert task.status == TaskStatus.COMPLETED, f"task status: {task.status}"
    assert "hello.py" in board.artifacts
    assert board.artifacts["hello.py"].status == "verified"
    assert board._final_summary

    written = tmp_path / "hello.py"
    assert written.exists()
    assert written.read_text() == "print('hello')\n"

    # Decision history should record the spawn → completed decision
    history_summary = board.decision_history.summary()
    assert history_summary["total_decisions"] >= 1
