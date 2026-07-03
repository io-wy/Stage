"""Regression: task-state sync must NOT depend on the agent-role prefix whitelist.

The Director can spawn one-off / dynamic roles (``spawn_agent`` / ``sub_agent``
``agent_spec``) whose id is NOT in ``infer_task_id``'s hard-coded prefix
whitelist (coder / reviewer / researcher / github_agent / monitor / director /
team_leader).

Before the fix, ``run_agent`` fired ``PATTERN_AFTER_EXECUTE`` WITHOUT a task_id,
so ``StateSyncHooks`` fell back to ``infer_task_id(agent_id)``:
- whitelisted "coder-t1"   → "t1"          → task marked COMPLETED  ✅
- dynamic    "auditor-t1"  → "auditor-t1"  → apply_outcome skips (not in tasks)
                                            → task stuck RUNNING, no error  ❌

This drives the REAL run_agent → hook → StateBoard chain with a non-whitelisted
agent_type and asserts the task is still synced. No real LLM (X-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openagents_orchestration.core.runner import OrchestratorRunner, RunnerDeps
from openagents_orchestration.core.state_board import Budget, StateBoard, TaskStatus
from openagents_orchestration.models.pattern import PatternOutcomeStatus
from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.utils.agent_id import infer_task_id


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
    """Replays a scripted sequence; tool-less planning calls get a canned plan."""

    _PLAN_JSON = '{"steps": ["do it"], "confidence": 8}'

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self._index = 0
        self.provider_name = "openai_compatible"

    async def generate(self, **kwargs: Any) -> Any:
        if kwargs.get("tools") is None:
            return FakeResponse(output_text=self._PLAN_JSON)
        if self._index >= len(self._responses):
            return FakeResponse(output_text="")
        response = self._responses[self._index]
        self._index += 1
        return response


def test_dynamic_role_id_is_outside_whitelist():
    """Guard the premise: a one-off role id is NOT stripped by infer_task_id."""
    assert infer_task_id("auditor-t1") == "auditor-t1"
    assert infer_task_id("coder-t1") == "t1"  # whitelisted, for contrast


@pytest.mark.asyncio
async def test_run_agent_syncs_task_for_non_whitelisted_role(tmp_path, monkeypatch):
    """A dynamic role's task must reach COMPLETED through the real hook chain."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openagents_orchestration.patterns.corecoder._detect_project_type",
        lambda cwd: {"type": "Python", "test_cmd": "python hello.py", "lint_cmd": ""},
    )

    objective = "Create hello.py via a dynamic role"
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
                    agent_type="auditor",  # ← deliberately OUTSIDE infer_task_id whitelist
                    input_context="Write hello.py that prints 'hello' when run.",
                    expected_artifacts=["hello.py"],
                ),
            ],
        )
    )

    config_path = Path(__file__).parent.parent / "agent.json"
    runner = OrchestratorRunner(config_path)
    runner._current_work_dir = tmp_path
    runner._state_board = board
    runner._state_sync_hooks.board = board  # hook needs the real board reference
    runner._deps = RunnerDeps(
        state_board=board,
        runner_delegate=runner.run_agent,
        runner=runner,
    )
    # Register the dynamic role by reusing the coder definition under a new name.
    assert "coder" in runner._agents_by_id, "coder role must exist to clone from"
    runner._agents_by_id["auditor"] = runner._agents_by_id["coder"]

    auditor_llm = FakeLLMClient(
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
        if agent_id == "auditor":
            bundle.llm_client = auditor_llm
        return bundle

    # Mirror what spawn_agent does before delegating to run_agent.
    board.register_agent("auditor-t1", "auditor")
    board.update_task("t1", status=TaskStatus.RUNNING)

    with patch.object(OrchestratorRunner, "_ensure_bundle", patched_ensure_bundle):
        outcome = await runner.run_agent(
            "auditor", "Create hello.py", agent_id="auditor-t1"
        )

    assert outcome.status == PatternOutcomeStatus.COMPLETED

    # Core regression assertion: a non-whitelisted role's task must still sync to
    # COMPLETED. Before the fix this stayed RUNNING (silent stuck task).
    task = board.get_task("t1")
    assert task.status == TaskStatus.COMPLETED, f"dynamic-role task not synced: {task.status}"

    written = tmp_path / "hello.py"
    assert written.exists()
    assert written.read_text() == "print('hello')\n"
