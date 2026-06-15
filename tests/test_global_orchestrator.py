"""Tests for GlobalOrchestrator enterprise multi-project management."""

from __future__ import annotations

import asyncio

import pytest

from openagents_orchestration.core.state_board import Budget
from openagents_orchestration.enterprise.global_orchestrator import GlobalOrchestrator
from openagents_orchestration.enterprise.project import ProjectStatus
from openagents_orchestration.enterprise.team import TeamSpec


class TestGlobalOrchestrator:
    @pytest.fixture
    def orchestrator(self, tmp_path):
        # Create a minimal agent.json for tests
        config_path = tmp_path / "agent.json"
        config_path.write_text(
            '{"version": "1.0", "logging": {"auto_configure": false}, '
            '"events": {"type": "async", "config": {}}, '
            '"runtime": {"type": "default", "config": {}}, "agents": []}'
        )
        return GlobalOrchestrator(config_path)

    def test_init(self, orchestrator):
        assert orchestrator._projects == {}
        assert orchestrator._global_budget.token_limit == -1
        assert orchestrator._enable_monitor is True

    def test_create_project(self, orchestrator):
        project = asyncio.run(
            orchestrator.create_project("Build API", budget=Budget(token_limit=1000))
        )
        assert project.project_id.startswith("proj-")
        assert project.objective == "Build API"
        assert project.status == ProjectStatus.PENDING
        assert project.budget is not None
        assert project.budget.token_limit == 1000
        assert project.project_id in orchestrator._projects

    def test_create_project_with_team_specs(self, orchestrator):
        project = asyncio.run(
            orchestrator.create_project(
                "Build API",
                team_specs=[
                    TeamSpec(name="backend", agent_types=["coder", "reviewer"]),
                    TeamSpec(name="docs", agent_types=["coder"]),
                ],
            )
        )
        teams = project.metadata.get("teams", {})
        assert len(teams) == 2

    def test_get_project(self, orchestrator):
        project = asyncio.run(orchestrator.create_project("Test"))
        fetched = orchestrator.get_project(project.project_id)
        assert fetched is project
        assert orchestrator.get_project("nonexistent") is None

    def test_list_projects(self, orchestrator):
        p1 = asyncio.run(orchestrator.create_project("P1"))
        p2 = asyncio.run(orchestrator.create_project("P2"))
        p1.status = ProjectStatus.RUNNING
        p2.status = ProjectStatus.PENDING

        all_projects = orchestrator.list_projects()
        assert len(all_projects) == 2

        running = orchestrator.list_projects(status=ProjectStatus.RUNNING)
        assert len(running) == 1
        assert running[0].project_id == p1.project_id

    def test_pause_resume_project(self, orchestrator):
        project = asyncio.run(orchestrator.create_project("Test"))
        project.start()
        asyncio.run(orchestrator.pause_project(project.project_id))
        assert project.status == ProjectStatus.PAUSED
        asyncio.run(orchestrator.resume_project(project.project_id))
        assert project.status == ProjectStatus.RUNNING

    def test_pause_nonexistent_project(self, orchestrator):
        with pytest.raises(ValueError):
            asyncio.run(orchestrator.pause_project("nonexistent"))

    def test_terminate_project(self, orchestrator):
        project = asyncio.run(orchestrator.create_project("Test"))
        project.start()
        asyncio.run(orchestrator.terminate_project(project.project_id))
        assert project.status == ProjectStatus.TERMINATED

    def test_human_channel(self, orchestrator):
        ch = orchestrator.human_channel
        qid = ch.ask("proj-1", "coder-1", "What model?")
        assert qid.startswith("hq-")
        assert len(ch.get_pending_questions()) == 1

        ch.answer(qid, "GPT-4")
        assert len(ch.get_answered_questions()) == 1

    def test_audit_log(self, orchestrator):
        asyncio.run(orchestrator.create_project("Test"))
        log = orchestrator.get_audit_log()
        entries = log.query(action="create_project")
        assert len(entries) == 1
        assert entries[0].actor == "global_orchestrator"

    def test_allocate_budget_unlimited(self, orchestrator):
        # Global budget is -1 (unlimited)
        requested = Budget(token_limit=5000, time_limit_s=600)
        allocated = orchestrator._allocate_budget(requested)
        assert allocated.token_limit == 5000
        assert allocated.time_limit_s == 600

    def test_to_dict(self, orchestrator):
        project = asyncio.run(orchestrator.create_project("Test"))
        project.start()
        d = orchestrator.to_dict()
        assert "projects" in d
        assert project.project_id in d["projects"]
        assert "global_budget" in d
        assert "human_channel" in d
        assert "audit_log" in d
