"""Tests for Project abstraction."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.enterprise.project import Project, ProjectStatus
from openagents_orchestration.core.state_board import Budget


class TestProject:
    def test_default_creation(self):
        p = Project(objective="build api")
        assert p.project_id.startswith("proj-")
        assert p.objective == "build api"
        assert p.status == ProjectStatus.PENDING
        assert p.state_board is not None
        assert p.state_board.objective == "build api"
        assert p.state_board.project_id == p.project_id
        assert p.artifact_store is not None
        assert p.work_dir.exists()

    def test_custom_budget(self):
        budget = Budget(token_limit=1000, time_limit_s=600)
        p = Project(objective="small task", budget=budget)
        assert p.budget.token_limit == 1000
        assert p.state_board.budget.token_limit == 1000

    def test_lifecycle_transitions(self):
        p = Project(objective="test")
        p.start()
        assert p.status == ProjectStatus.RUNNING
        p.pause()
        assert p.status == ProjectStatus.PAUSED
        p.resume()
        assert p.status == ProjectStatus.RUNNING

    def test_add_tasks(self):
        p = Project(objective="test")
        graph = TaskGraph(
            objective="test",
            tasks=[TaskNode("t1", "fix bug", "coder")],
        )
        p.add_tasks(graph)
        assert "t1" in p.state_board.tasks

    def test_to_from_dict(self):
        p = Project(objective="test", budget=Budget(token_limit=500))
        p.start()
        p.add_tasks(
            TaskGraph(
                objective="test",
                tasks=[TaskNode("t1", "task", "coder")],
            )
        )

        d = p.to_dict()
        restored = Project.from_dict(d)

        assert restored.project_id == p.project_id
        assert restored.objective == p.objective
        assert restored.status == p.status
        assert restored.state_board is not None
        assert "t1" in restored.state_board.tasks
        assert restored.budget is not None

    def test_work_dir_creation(self, tmp_path):
        p = Project(objective="test", work_dir=tmp_path / "custom")
        assert p.work_dir == tmp_path / "custom"
        assert p.work_dir.exists()
        assert (p.work_dir / ".artifacts").exists()

    @pytest.mark.asyncio
    async def test_terminate(self):
        p = Project(objective="test")
        p.start()
        report = await p.terminate("done")
        assert p.status == ProjectStatus.TERMINATED
        assert report is not None
