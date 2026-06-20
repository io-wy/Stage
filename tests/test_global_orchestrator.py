"""Adversarial tests for GlobalOrchestrator — the enterprise multi-project entry.

Module under test: ``src/openagents_orchestration/enterprise/global_orchestrator.py``
(385 lines, zero coverage — old ``test_global_orchestrator.py`` was deleted). The
class advertises a *global budget* that caps per-project allocations (CLAUDE.md §2
lists this as a core design rationale), multi-project isolation, and lifecycle
management. ``test_gap_*`` pin where those guarantees are missing.

All Project construction is redirected to ``tmp_path`` because ``Project.__post_init__``
does real ``mkdir``.
"""

from __future__ import annotations

import pytest

from openagents_orchestration.core.state_board import Budget
from openagents_orchestration.enterprise.global_orchestrator import GlobalOrchestrator
from openagents_orchestration.enterprise.project import ProjectStatus
from openagents_orchestration.enterprise.team import TeamSpec
from openagents_orchestration.models.delivery import DeliveryReport, TaskResult


class _FakeRunner:
    """Stand-in for OrchestratorRunner so run() does no real LLM work."""

    last_report: DeliveryReport | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.state_board = None  # skips metrics collection in run()

    async def run(self, **kwargs) -> DeliveryReport:
        return _FakeRunner.last_report or DeliveryReport(objective=kwargs.get("objective", ""))


@pytest.fixture
def patched_runner(monkeypatch):
    from openagents_orchestration.enterprise import global_orchestrator as go_mod

    monkeypatch.setattr(go_mod, "OrchestratorRunner", _FakeRunner)
    return _FakeRunner


# ── lifecycle baseline (the parts that work) ──────────────────────────────────


async def test_create_project_registers_and_audits(tmp_path):
    go = GlobalOrchestrator(tmp_path / "agent.json")
    proj = await go.create_project("build a thing", work_dir=str(tmp_path / "p1"))
    assert go.get_project(proj.project_id) is proj
    assert proj.status == ProjectStatus.PENDING
    created = go.get_audit_log().query(action="create_project")
    assert len(created) == 1 and created[0].target == proj.project_id


async def test_list_projects_filters_by_status(tmp_path):
    go = GlobalOrchestrator(tmp_path / "agent.json")
    p1 = await go.create_project("a", work_dir=str(tmp_path / "a"))
    p2 = await go.create_project("b", work_dir=str(tmp_path / "b"))
    p2.status = ProjectStatus.RUNNING
    assert len(go.list_projects()) == 2
    running = go.list_projects(status=ProjectStatus.RUNNING)
    assert running == [p2]
    assert go.list_projects(status=ProjectStatus.PENDING) == [p1]


async def test_pause_resume_and_unknown_project_raises(tmp_path):
    go = GlobalOrchestrator(tmp_path / "agent.json")
    proj = await go.create_project("x", work_dir=str(tmp_path / "x"))
    await go.pause_project(proj.project_id)
    assert proj.status == ProjectStatus.PAUSED
    await go.resume_project(proj.project_id)
    assert proj.status == ProjectStatus.RUNNING
    with pytest.raises(ValueError):
        await go.pause_project("does-not-exist")


# ── GAP 1: the global budget can never be set → the cap branch is dead code ───


def test_gap_global_budget_unsettable_and_cap_branch_unreachable():
    """``__init__`` hardcodes the global budget to unlimited (-1) with no
    constructor param and no setter, so ``_allocate_budget`` ALWAYS takes the
    pass-through branch — the 'cap at remaining global budget' code (and the
    ``_GlobalBudget`` allocation-tracking subclass) is unreachable. The global
    budget enforcement that CLAUDE.md §2 calls a core rationale does not exist."""
    go = GlobalOrchestrator("/nonexistent/agent.json")
    assert go._global_budget.token_limit == -1
    assert not hasattr(go, "set_global_budget")  # no supported way to set it

    # Every allocation grants the full request; the pool is never decremented:
    for _ in range(5):
        alloc = go._allocate_budget(Budget(token_limit=1_000_000))
        assert alloc.token_limit == 1_000_000
    assert go._global_budget.token_used == 0


def test_gap_allocate_budget_ignores_global_time_limit():
    """Even the token cap aside, ``_allocate_budget`` never caps ``time_limit_s``
    against the global budget — only ``token_limit`` is (theoretically) capped."""
    go = GlobalOrchestrator("/nonexistent/agent.json")
    alloc = go._allocate_budget(Budget(token_limit=100, time_limit_s=99_999.0))
    assert alloc.time_limit_s == 99_999.0  # passed through unbounded


# ── GAP 2: run() marks the project COMPLETED regardless of delivery outcome ────


async def test_gap_run_marks_completed_even_on_failed_delivery(tmp_path, patched_runner):
    """``run()`` sets ``ProjectStatus.COMPLETED`` unconditionally (and records a
    'project.completed' audit) even when every task failed. Project status no
    longer reflects reality, and there is no FAILED branch."""
    patched_runner.last_report = DeliveryReport(
        objective="do x",
        task_results=[TaskResult(task_id="t1", status="failed", error="boom")],
    )
    go = GlobalOrchestrator(tmp_path / "agent.json")
    report = await go.run("do x", work_dir=str(tmp_path / "run1"))

    assert report.all_succeeded is False  # the delivery clearly failed
    proj = go.list_projects()[0]
    assert proj.status == ProjectStatus.COMPLETED  # GAP: marked completed anyway
    assert len(go.get_audit_log().query(event_type="project.completed")) == 1


# ── GAP 3: an explicit project_id that collides silently clobbers the prior one ─


async def test_gap_explicit_project_id_collision_clobbers_previous(tmp_path, patched_runner):
    """Passing the same explicit ``project_id`` to two runs makes the second
    ``self._projects[pid] = self._projects.pop(old_id)`` overwrite the first
    project (and leak its auto-created work_dir). No collision check exists."""
    go = GlobalOrchestrator(tmp_path / "agent.json")
    await go.run("first", project_id="shared", work_dir=str(tmp_path / "a"))
    await go.run("second", project_id="shared", work_dir=str(tmp_path / "b"))

    projs = go.list_projects()
    assert len(projs) == 1  # GAP: the first project is gone
    assert go.get_project("shared").objective == "second"


# ── GAP 4: teams are created (with side effects) then thrown away ─────────────


async def test_gap_teams_created_but_live_objects_discarded(tmp_path):
    """``create_project(team_specs=...)`` builds live ``Team`` objects (each with
    a SubStateBoard) but keeps only ``team.to_dict()`` in metadata — the live
    objects are discarded, so teams can never be scheduled or stopped. The code
    comment even admits 'Project doesn't have teams dict yet'."""
    go = GlobalOrchestrator(tmp_path / "agent.json")
    proj = await go.create_project(
        "obj",
        work_dir=str(tmp_path / "p"),
        team_specs=[TeamSpec(name="backend", agent_types=["coder", "reviewer"])],
    )
    # The snapshot exists...
    assert "teams" in proj.metadata and len(proj.metadata["teams"]) == 1
    # ...but there is no live handle: no project.teams, no go.get_team().
    assert not hasattr(proj, "teams")
    assert not hasattr(go, "get_team")
