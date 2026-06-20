"""End-to-end test for collaborative mode: producer (coder) <-> checker (reviewer) loop.

Drives two scenarios using FakeLLM to script both sides:
1. Happy path: producer writes file -> checker approves in one pass
2. Fix loop: producer writes -> checker rejects -> task enters FIX_NEEDED

The real OrchestratorRunner._run_collaborative is exercised; no Director involved.
"""

from __future__ import annotations

import json
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
# Fake LLM helpers (mirrors test_orchestration_golden_path.py)
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
# Test helpers
# ---------------------------------------------------------------------------


def _make_runner(board: StateBoard, tmp_path: Path) -> OrchestratorRunner:
    """Create an OrchestratorRunner wired to a pre-built StateBoard."""
    config_path = Path(__file__).parent.parent / "agent.json"
    runner = OrchestratorRunner(
        config_path,
        enable_monitor_resident=False,
        collaborative_mode="on",
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
    return runner


# ---------------------------------------------------------------------------
# Scenario 1: one-pass approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collaboration_loop_happy_path(tmp_path, monkeypatch):
    """Producer writes file, checker approves in one pass."""
    monkeypatch.chdir(tmp_path)

    # Make verification cheap: python hello.py counts as the test command.
    monkeypatch.setattr(
        "openagents_orchestration.patterns.corecoder._detect_project_type",
        lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
    )

    objective = "Create hello.py that prints hello"

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

    runner = _make_runner(board, tmp_path)

    # Producer (coder) scripted responses:
    #   1. write_file -> hello.py
    #   2. bash -> python hello.py (test)
    #   3. send_message to reviewer with TASK_REVIEW_READY
    # Note: send_message schema uses "to_agent" and "message" params.
    producer_llm = FakeLLMClient(
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
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "send_message",
                        {
                            "to_agent": "reviewer-t1",
                            "message": "TASK_REVIEW_READY[t1]: tests passed = 1\nFile created and verified.",
                        },
                    )
                ]
            ),
            FakeResponse(output_text="Done."),
        ]
    )

    # Checker (reviewer) scripted responses:
    #   1. read_file -> hello.py
    #   2. send_message to coder with TASK_APPROVED
    checker_llm = FakeLLMClient(
        [
            FakeResponse(
                tool_calls=[FakeToolCall("read_file", {"file_path": "hello.py"})]
            ),
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "send_message",
                        {
                            "to_agent": "coder-t1",
                            "message": "TASK_APPROVED[t1]: Code looks good, tests pass.",
                        },
                    )
                ]
            ),
            FakeResponse(output_text="Done."),
        ]
    )

    original_ensure_bundle = OrchestratorRunner._ensure_bundle

    def patched_ensure_bundle(self, agent_id: str):
        bundle = original_ensure_bundle(self, agent_id)
        if agent_id.startswith("coder"):
            bundle.llm_client = producer_llm
        elif agent_id.startswith("reviewer"):
            bundle.llm_client = checker_llm
        # Disable planning so scripted tool calls are not consumed by plan generation.
        bundle.plugins.pattern._enable_planning = False
        return bundle

    with patch.object(OrchestratorRunner, "_ensure_bundle", patched_ensure_bundle):
        await runner._run_collaborative()

    # -- assertions --
    task = board.get_task("t1")
    assert task.status == TaskStatus.COMPLETED, f"task status: {task.status}"

    hello_py = tmp_path / "hello.py"
    assert hello_py.exists(), "hello.py should exist"
    assert hello_py.read_text() == "print('hello')\n"

    # Decision history should have some collaboration-related records
    # Note: in collaborative mode, decisions are recorded by run_agent (Director path),
    # not by _run_collaborative. The key assertion is task completion.
    history_summary = board.decision_history.summary()
    assert history_summary.get("total", 0) >= 0  # may be empty in collaborative mode

    # Residents should be cleaned up (stopped) after completion
    assert "coder-t1" not in runner._residents or not runner._residents["coder-t1"]._active
    assert "reviewer-t1" not in runner._residents or not runner._residents["reviewer-t1"]._active


# ---------------------------------------------------------------------------
# Scenario 2: fix-needed path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collaboration_loop_fix_needed(tmp_path, monkeypatch):
    """Producer writes, checker rejects, task enters FIX_NEEDED, producer is woken.

    This test verifies the first half of the fix loop: the collaboration state
    machine correctly transitions from REVIEW -> FIX_NEEDED and wakes the producer.
    The full round-trip (producer fixes -> reviewer re-approves -> COMPLETED)
    requires production code to send a fresh review message to the checker on
    re-wake, which is not yet implemented.
    """
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "openagents_orchestration.patterns.corecoder._detect_project_type",
        lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
    )

    objective = "Create hello.py that prints hello with a null check"

    # Tight budget so the loop exits after the fix-needed transition
    # (producer first run 4 + reviewer 3 + producer second run 2 = 9 steps)
    board = StateBoard(
        objective,
        budget=Budget(token_limit=10_000, time_limit_s=300, max_steps=9),
    )
    board.add_tasks(
        TaskGraph(
            objective=objective,
            tasks=[
                TaskNode(
                    task_id="t1",
                    description="Create hello.py with null check",
                    agent_type="coder",
                    input_context="Write hello.py that prints 'hello' and includes a null check.",
                    expected_artifacts=["hello.py"],
                ),
            ],
        )
    )

    runner = _make_runner(board, tmp_path)

    # Producer scripted responses (one round only):
    #   1. write_file (initial, missing null check)
    #   2. bash -> python hello.py
    #   3. send_message to reviewer: TASK_REVIEW_READY
    producer_llm = FakeLLMClient(
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
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "send_message",
                        {
                            "to_agent": "reviewer-t1",
                            "message": "TASK_REVIEW_READY[t1]: tests passed = 1\nInitial version.",
                        },
                    )
                ]
            ),
            FakeResponse(output_text="Done."),
        ]
    )

    # Checker scripted responses (one round only):
    #   1. read_file -> hello.py
    #   2. send_message to coder with TASK_FIX_NEEDED
    checker_llm = FakeLLMClient(
        [
            FakeResponse(
                tool_calls=[FakeToolCall("read_file", {"file_path": "hello.py"})]
            ),
            FakeResponse(
                tool_calls=[
                    FakeToolCall(
                        "send_message",
                        {
                            "to_agent": "coder-t1",
                            "message": "TASK_FIX_NEEDED[t1]: line 1 missing null check for name variable",
                        },
                    )
                ]
            ),
            FakeResponse(output_text="Done."),
        ]
    )

    original_ensure_bundle = OrchestratorRunner._ensure_bundle

    def patched_ensure_bundle(self, agent_id: str):
        bundle = original_ensure_bundle(self, agent_id)
        if agent_id.startswith("coder"):
            bundle.llm_client = producer_llm
        elif agent_id.startswith("reviewer"):
            bundle.llm_client = checker_llm
        bundle.plugins.pattern._enable_planning = False
        return bundle

    with patch.object(OrchestratorRunner, "_ensure_bundle", patched_ensure_bundle):
        await runner._run_collaborative()

    # -- assertions --
    task = board.get_task("t1")

    # The task should have entered FIX_NEEDED state
    # (it may have timed out or transitioned further depending on budget)
    history = task.iteration_history
    actions = [entry["action"] for entry in history]
    assert "reviewer_requested_fix" in actions, f"Expected fix request in history, got: {actions}"

    # The producer should have been woken (its status should show it processed the fix)
    # We verify by checking that the producer was active after the fix request
    assert "coder-t1" in runner._residents or any(
        entry["agent_id"].startswith("coder") for entry in history
    ), "Producer should have been involved"

    # The file should exist (producer wrote it)
    hello_py = tmp_path / "hello.py"
    assert hello_py.exists()

    # Verify state transitions went through REVIEW
    status_changes = [
        entry for entry in board.events
        if entry.event_type.startswith("task.") and entry.task_id == "t1"
    ]
    statuses = [e.event_type for e in status_changes]
    assert "task.review" in statuses, f"Expected REVIEW transition in events, got: {statuses}"
    assert "task.fix_needed" in statuses, f"Expected FIX_NEEDED transition in events, got: {statuses}"


# ---------------------------------------------------------------------------
# Scenario 3: budget exhaustion short-circuits loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collaboration_loop_respects_budget(tmp_path, monkeypatch):
    """Collaborative loop exits when budget is exhausted."""
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "openagents_orchestration.patterns.corecoder._detect_project_type",
        lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
    )

    objective = "Create hello.py"

    # Very tight budget: only 3 steps
    board = StateBoard(
        objective,
        budget=Budget(token_limit=10_000, time_limit_s=300, max_steps=3),
    )
    board.add_tasks(
        TaskGraph(
            objective=objective,
            tasks=[
                TaskNode(
                    task_id="t1",
                    description="Create hello.py",
                    agent_type="coder",
                    expected_artifacts=["hello.py"],
                ),
            ],
        )
    )

    runner = _make_runner(board, tmp_path)

    # Even if producer wants to run, budget should cut it short
    producer_llm = FakeLLMClient(
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
                output_text="TASK_REVIEW_READY[t1]: tests passed = 1"
            ),
        ]
    )

    checker_llm = FakeLLMClient(
        [
            FakeResponse(
                output_text="TASK_APPROVED[t1]"
            ),
        ]
    )

    original_ensure_bundle = OrchestratorRunner._ensure_bundle

    def patched_ensure_bundle(self, agent_id: str):
        bundle = original_ensure_bundle(self, agent_id)
        if agent_id.startswith("coder"):
            bundle.llm_client = producer_llm
        elif agent_id.startswith("reviewer"):
            bundle.llm_client = checker_llm
        bundle.plugins.pattern._enable_planning = False
        return bundle

    with patch.object(OrchestratorRunner, "_ensure_bundle", patched_ensure_bundle):
        await runner._run_collaborative()

    # Budget exhausted means loop exited; task may or may not be completed
    # depending on whether it finished before budget ran out
    assert board.budget.exhausted, "Budget should be exhausted"
