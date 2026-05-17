"""Tests for phased_run.py — phase-by-phase orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.state_board import StateBoard


class TestPlanPhase:
    @pytest.mark.asyncio
    async def test_plan_decomposes_and_saves(self, tmp_path: Path):
        from phased_run import cmd_plan

        plan_file = tmp_path / "plan.json"

        # Mock _initial_decompose to return a known graph
        mock_graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "design API", "coder", expected_artifacts=["api.md"]),
                TaskNode("t2", "implement models", "coder", dependencies=["t1"]),
            ],
        )

        with patch(
            "phased_run.OrchestratorRunner._initial_decompose",
            new_callable=AsyncMock,
            return_value=mock_graph,
        ):
            rc = await cmd_plan("test objective", plan_file)

        assert rc == 0
        assert plan_file.exists()
        data = json.loads(plan_file.read_text(encoding="utf-8"))
        assert data["objective"] == "test"
        assert len(data["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_plan_validates_graph(self, tmp_path: Path):
        from phased_run import cmd_plan

        plan_file = tmp_path / "plan.json"
        # Graph with cycle
        mock_graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "a", "coder", dependencies=["t2"]),
                TaskNode("t2", "b", "coder", dependencies=["t1"]),
            ],
        )

        with patch(
            "phased_run.OrchestratorRunner._initial_decompose",
            new_callable=AsyncMock,
            return_value=mock_graph,
        ):
            with pytest.raises(ValueError, match="circular"):
                await cmd_plan("test", plan_file)


class TestReviewPlanPhase:
    @pytest.mark.asyncio
    async def test_review_plan_reads_and_validates(self, tmp_path: Path):
        from phased_run import cmd_review_plan

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "design", "coder", expected_artifacts=["a.md"], input_context="ctx"),
                TaskNode("t2", "code", "coder", dependencies=["t1"]),
            ],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        rc = await cmd_review_plan(plan_file)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_review_plan_detects_invalid_plan(self, tmp_path: Path):
        from phased_run import cmd_review_plan

        plan_file = tmp_path / "plan.json"
        # Unknown dependency
        graph = TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "a", "coder", dependencies=["nonexistent"])],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        rc = await cmd_review_plan(plan_file)
        assert rc == 1

    @pytest.mark.asyncio
    async def test_review_plan_shows_execution_state(self, tmp_path: Path):
        from phased_run import cmd_review_plan

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "design", "coder")],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        # Write state file
        state_file = tmp_path / "plan.state.json"
        state_file.write_text(json.dumps({"tasks": {"t1": {"status": "completed"}}}), encoding="utf-8")

        rc = await cmd_review_plan(plan_file)
        assert rc == 0


class TestSpawnPhase:
    @pytest.mark.asyncio
    async def test_spawn_runs_agent(self):
        from phased_run import cmd_spawn

        with patch(
            "phased_run.OrchestratorRunner.run_agent",
            new_callable=AsyncMock,
            return_value="Done. FILES_CREATED: hello.py",
        ) as mock_run:
            rc = await cmd_spawn("coder", "write hello.py")

        assert rc == 0
        mock_run.assert_awaited_once_with("coder", "write hello.py")

    @pytest.mark.asyncio
    async def test_spawn_reads_from_file(self, tmp_path: Path):
        from phased_run import cmd_spawn

        input_file = tmp_path / "task.txt"
        input_file.write_text("write hello.py", encoding="utf-8")

        with patch(
            "phased_run.OrchestratorRunner.run_agent",
            new_callable=AsyncMock,
            return_value="Done",
        ) as mock_run:
            rc = await cmd_spawn("coder", f"@{input_file}")

        assert rc == 0
        mock_run.assert_awaited_once_with("coder", "write hello.py")


class TestExecuteTaskPhase:
    @pytest.mark.asyncio
    async def test_execute_task_runs_single_task(self, tmp_path: Path):
        from phased_run import cmd_execute_task

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "write hello", "coder", input_context="write hello.py"),
            ],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        with patch(
            "phased_run.SpawnAgentTool.invoke",
            new_callable=AsyncMock,
            return_value={"status": "completed", "artifacts": ["hello.py"]},
        ) as mock_invoke:
            rc = await cmd_execute_task(plan_file, "t1")

        assert rc == 0
        mock_invoke.assert_awaited_once()
        call_args = mock_invoke.call_args
        assert call_args[0][0] == {"task_id": "t1"}

        # State should be saved
        state_file = tmp_path / "plan.state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["tasks"]["t1"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_task_skips_non_pending(self, tmp_path: Path):
        from phased_run import cmd_execute_task

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "write hello", "coder")],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        # Pre-mark as completed
        state_file = tmp_path / "plan.state.json"
        state_file.write_text(json.dumps({"tasks": {"t1": {"status": "completed"}}}), encoding="utf-8")

        rc = await cmd_execute_task(plan_file, "t1")
        assert rc == 0  # Skipped, not error

    @pytest.mark.asyncio
    async def test_execute_task_blocks_on_unmet_deps(self, tmp_path: Path):
        from phased_run import cmd_execute_task

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "design", "coder"),
                TaskNode("t2", "implement", "coder", dependencies=["t1"]),
            ],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        rc = await cmd_execute_task(plan_file, "t2")
        assert rc == 1


class TestStepPhase:
    @pytest.mark.asyncio
    async def test_step_executes_ready_tasks(self, tmp_path: Path):
        from phased_run import cmd_step

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "write hello", "coder", input_context="write hello.py"),
                TaskNode("t2", "write world", "coder", input_context="write world.py"),
                TaskNode("t3", "combine", "coder", dependencies=["t1", "t2"]),
            ],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        with patch(
            "phased_run.SpawnAgentTool.invoke",
            new_callable=AsyncMock,
            return_value={"status": "completed", "artifacts": ["file.py"]},
        ) as mock_invoke:
            rc = await cmd_step(plan_file)

        assert rc == 0
        # t1 and t2 should both be spawned (independent, ready)
        assert mock_invoke.await_count == 2

        # State should show t1, t2 completed
        state_file = tmp_path / "plan.state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["tasks"]["t1"]["status"] == "completed"
        assert state["tasks"]["t2"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_step_respects_partial_state(self, tmp_path: Path):
        from phased_run import cmd_step

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[
                TaskNode("t1", "write hello", "coder"),
                TaskNode("t2", "write world", "coder", dependencies=["t1"]),
            ],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        # t1 already done
        state_file = tmp_path / "plan.state.json"
        state_file.write_text(json.dumps({"tasks": {"t1": {"status": "completed"}}}), encoding="utf-8")

        with patch(
            "phased_run.SpawnAgentTool.invoke",
            new_callable=AsyncMock,
            return_value={"status": "completed", "artifacts": []},
        ) as mock_invoke:
            rc = await cmd_step(plan_file)

        assert rc == 0
        assert mock_invoke.await_count == 1  # Only t2

    @pytest.mark.asyncio
    async def test_step_no_ready_tasks(self, tmp_path: Path):
        from phased_run import cmd_step

        plan_file = tmp_path / "plan.json"
        graph = TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "write hello", "coder")],
        )
        plan_file.write_text(json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8")

        # t1 already done
        state_file = tmp_path / "plan.state.json"
        state_file.write_text(json.dumps({"tasks": {"t1": {"status": "completed"}}}), encoding="utf-8")

        rc = await cmd_step(plan_file)
        assert rc == 0
