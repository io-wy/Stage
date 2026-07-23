"""GlobalOrchestrator — legacy multi-project orchestration entry point.

Manages multiple Projects, each with their own StateBoard, Budget, and Teams.
Maintains a global budget and HumanChannel.

Compatibility:
- OrchestratorRunner is preserved as a single-project alias
- All existing APIs continue to work unchanged
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents_orchestration.models.delivery import DeliveryReport
from openagents_orchestration.runtime.human_channel import HumanChannel
from openagents_orchestration.runtime.project import Project, ProjectStatus
from openagents_orchestration.runtime.runner import OrchestratorRunner
from openagents_orchestration.runtime.security import AuditLog
from openagents_orchestration.runtime.state_board import Budget, StateBoard
from openagents_orchestration.runtime.team import Team, TeamSpec
from openagents_orchestration.transport.channel_policy import (
    DEFAULT_GLOBAL_POLICY,
)


@dataclass
class _GlobalBudget(Budget):
    """Global budget that tracks usage across all projects."""

    project_allocations: dict[str, Budget] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.project_allocations is None:
            self.project_allocations = {}


class GlobalOrchestrator:
    """Enterprise orchestrator managing multiple isolated Projects.

    Usage::

        orchestrator = GlobalOrchestrator("agent.json")
        report = await orchestrator.run(
            "Build a FastAPI TODO API",
            budget=Budget(token_limit=100_000, time_limit_s=3600),
            team_specs=[
                TeamSpec(name="backend", agent_types=["coder", "reviewer"]),
            ],
        )
    """

    def __init__(
        self,
        config_path: str | Path,
        *,
        global_budget: Budget | None = None,
    ):
        self._config_path = Path(config_path)
        self._projects: dict[str, Project] = {}
        self._global_budget = _GlobalBudget(
            token_limit=getattr(global_budget, "token_limit", -1),
            time_limit_s=getattr(global_budget, "time_limit_s", -1),
            max_steps=getattr(global_budget, "max_steps", -1),
        )
        self._human_channel = HumanChannel()
        self._audit_log = AuditLog()

        # Single-runner cache for backward-compat run() path
        self._runner: OrchestratorRunner | None = None
        self._runner_lock = asyncio.Lock()

    # -- public API: single-project run (backward compatible) ------------------

    async def run(
        self,
        objective: str,
        *,
        project_id: str | None = None,
        budget: Budget | None = None,
        work_dir: str | None = None,
        team_specs: list[TeamSpec] | None = None,
        **runner_kwargs: Any,
    ) -> DeliveryReport:
        """Run a single project (backward-compatible with OrchestratorRunner.run).

        This is a convenience wrapper that:
        1. Creates a Project
        2. Optionally creates Teams
        3. Delegates to OrchestratorRunner for execution
        4. Returns the DeliveryReport
        """
        project = await self.create_project(
            objective=objective,
            budget=budget,
            work_dir=work_dir,
            team_specs=team_specs,
        )
        if project_id:
            # Override auto-generated project_id
            old_id = project.project_id
            project.project_id = project_id
            self._projects[project_id] = self._projects.pop(old_id)

        async with self._runner_lock:
            self._runner = OrchestratorRunner(
                self._config_path,
            )
            # Wire human channel into project state board
            if project.state_board is not None:
                project.state_board.human_channel_service.channel = self._human_channel

            try:
                report = await self._runner.run(
                    objective=objective,
                    budget=budget,
                    work_dir=work_dir,
                    **runner_kwargs,
                )
            finally:
                if hasattr(self._runner, "close"):
                    await self._runner.close()

        # Update project status
        project.status = ProjectStatus.COMPLETED
        self._audit_log.record(
            event_type="project.completed",
            actor="global_orchestrator",
            action="run",
            target=project.project_id,
        )

        return report

    # -- project management ----------------------------------------------------

    async def create_project(
        self,
        objective: str,
        *,
        budget: Budget | None = None,
        work_dir: str | None = None,
        team_specs: list[TeamSpec] | None = None,
    ) -> Project:
        """Create and register a new Project."""
        project = Project(
            objective=objective,
            budget=budget,
            work_dir=Path(work_dir) if work_dir else None,
        )

        # Wire human channel
        if project.state_board is not None:
            project.state_board.human_channel_service.channel = self._human_channel

        # Allocate from global budget
        allocated = self._allocate_budget(project.project_id, budget)
        if project.budget is None:
            project.budget = allocated

        # Create teams if specified
        if team_specs:
            for spec in team_specs:
                team = self._create_team(project, spec)
                # Store in project metadata (Project doesn't have teams dict yet)
                project.metadata.setdefault("teams", {})[team.team_id] = team.to_dict()

        self._projects[project.project_id] = project
        self._audit_log.record(
            event_type="project.created",
            actor="global_orchestrator",
            action="create_project",
            target=project.project_id,
            objective=objective,
        )
        return project

    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def list_projects(
        self, status: ProjectStatus | None = None
    ) -> list[Project]:
        result = list(self._projects.values())
        if status is not None:
            result = [p for p in result if p.status == status]
        return result

    async def pause_project(self, project_id: str) -> None:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        project.pause()
        self._audit_log.record(
            event_type="project.paused",
            actor="global_orchestrator",
            action="pause_project",
            target=project_id,
        )

    async def resume_project(self, project_id: str) -> None:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        project.resume()
        self._audit_log.record(
            event_type="project.resumed",
            actor="global_orchestrator",
            action="resume_project",
            target=project_id,
        )

    async def terminate_project(
        self, project_id: str, reason: str = ""
    ) -> DeliveryReport | None:
        project = self._projects.get(project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        report = await project.terminate(reason)
        self._release_budget(project_id, project)
        self._audit_log.record(
            event_type="project.terminated",
            actor="global_orchestrator",
            action="terminate_project",
            target=project_id,
            reason=reason,
        )
        return report

    # -- team management -------------------------------------------------------

    def _create_team(self, project: Project, spec: TeamSpec) -> Team:
        """Create a Team within a Project."""
        team_id = f"team-{uuid.uuid4().hex[:8]}"

        # Create a scoped board for the team.
        if project.state_board is not None:
            sub_board = StateBoard(
                objective=f"{spec.name}: {project.objective}",
                budget=Budget(
                    token_limit=max(1, project.state_board.budget.token_remaining // 4),
                    time_limit_s=max(60.0, project.state_board.budget.time_remaining_s / 4),
                    max_steps=30,
                ),
                echo=False,
                human_channel_service=project.state_board.human_channel_service,
            )
        else:
            sub_board = StateBoard(
                objective=f"{spec.name}: {project.objective}",
                budget=Budget(max_steps=30),
                echo=False,
            )

        team = Team(
            team_id=team_id,
            project_id=project.project_id,
            name=spec.name,
            state_board=sub_board,
            channel_policy=spec.channel_policy or DEFAULT_GLOBAL_POLICY,
        )

        if project.state_board is not None:
            project.state_board.log_event(
                "team.created",
                message=f"Team {team_id} ({spec.name}) created",
                team_id=team_id,
            )

        return team

    # -- human channel ---------------------------------------------------------

    @property
    def human_channel(self) -> HumanChannel:
        return self._human_channel

    # -- internal --------------------------------------------------------------

    def _allocate_budget(
        self, project_id: str, requested: Budget | None
    ) -> Budget:
        """Allocate a project budget from the global budget pool."""
        if requested is None:
            return Budget()
        # If global budget is unlimited (-1), pass through
        if self._global_budget.token_limit < 0:
            allocated = Budget(
                token_limit=requested.token_limit,
                time_limit_s=requested.time_limit_s,
                max_steps=requested.max_steps,
            )
        else:
            # Cap at remaining global budget and charge the allocation.
            available = self._global_budget.token_remaining
            cap = min(requested.token_limit, available)
            allocated = Budget(
                token_limit=cap,
                time_limit_s=requested.time_limit_s,
                max_steps=requested.max_steps,
            )
            self._global_budget.token_used += cap
        self._global_budget.project_allocations[project_id] = allocated
        return allocated

    def _release_budget(self, project_id: str, project: Project) -> None:
        """Return unused allocated tokens to the global budget on project end."""
        allocated = self._global_budget.project_allocations.pop(project_id, None)
        if allocated is None or self._global_budget.token_limit < 0:
            return
        used = getattr(project.budget, "token_used", 0)
        unused = max(0, allocated.token_limit - used)
        self._global_budget.token_used -= unused

    def get_audit_log(self) -> AuditLog:
        return self._audit_log

    # -- shutdown --------------------------------------------------------------

    async def shutdown(self) -> None:
        """Gracefully shut down all projects."""
        for project in list(self._projects.values()):
            if project.status in (ProjectStatus.RUNNING, ProjectStatus.PAUSED):
                await project.terminate("Global orchestrator shutdown")
            self._release_budget(project.project_id, project)

    # -- snapshot --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "projects": {
                pid: p.to_dict() for pid, p in self._projects.items()
            },
            "global_budget": self._global_budget.to_dict(),
            "human_channel": self._human_channel.to_dict(),
            "audit_log": self._audit_log.to_dict(),
        }
