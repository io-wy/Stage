"""Project — isolated execution unit for enterprise orchestration.

A Project owns its own StateBoard, Budget, TaskGraph, and Teams.
It is the bridge between the enterprise-level GlobalOrchestrator and the
existing single-project StateBoard architecture.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from openagents_orchestration.core.state_board import Budget, StateBoard
from openagents_orchestration.models.delivery import DeliveryReport
from openagents_orchestration.models.task import TaskGraph


class ProjectStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass
class Project:
    """An isolated orchestration project."""

    objective: str
    project_id: str = field(default_factory=lambda: f"proj-{uuid.uuid4().hex[:8]}")
    budget: Budget | None = None
    work_dir: Path | None = None
    state_board: StateBoard | None = field(default=None, repr=False)
    status: ProjectStatus = ProjectStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.work_dir is None:
            self.work_dir = Path(f".projects/{self.project_id}")
        self.work_dir = Path(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        if self.state_board is None:
            self.state_board = StateBoard(
                objective=self.objective,
                budget=self.budget,
                echo=True,
            )

        # Back-reference for tools that need project context
        self.state_board.project_id = self.project_id  # type: ignore[attr-defined]

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Transition project to RUNNING."""
        self.status = ProjectStatus.RUNNING
        if self.state_board is not None:
            self.state_board.log_event(
                "project.started",
                message=f"Project {self.project_id} started",
            )

    def pause(self) -> None:
        """Transition project to PAUSED."""
        self.status = ProjectStatus.PAUSED
        if self.state_board is not None:
            self.state_board.log_event(
                "project.paused",
                message=f"Project {self.project_id} paused",
            )

    def resume(self) -> None:
        """Transition project back to RUNNING."""
        self.status = ProjectStatus.RUNNING
        if self.state_board is not None:
            self.state_board.log_event(
                "project.resumed",
                message=f"Project {self.project_id} resumed",
            )

    async def terminate(self, reason: str = "") -> DeliveryReport | None:
        """Terminate the project and return a delivery report if available."""
        self.status = ProjectStatus.TERMINATED
        if self.state_board is not None:
            self.state_board.log_event(
                "project.terminated",
                message=f"Project {self.project_id} terminated: {reason}"[:200],
            )
            if hasattr(self.state_board, "to_report"):
                return self.state_board.to_report()
        return None

    # -- task management -------------------------------------------------------

    def add_tasks(self, graph: TaskGraph) -> None:
        """Import tasks into the project's StateBoard."""
        if self.state_board is None:
            raise RuntimeError("StateBoard not initialized")
        self.state_board.add_tasks(graph)

    # -- snapshot --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize project metadata and state board snapshot."""
        return {
            "project_id": self.project_id,
            "objective": self.objective,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
            "work_dir": str(self.work_dir),
            "budget": self.budget.to_dict() if self.budget else None,
            "state_board": self.state_board.to_dict() if self.state_board else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        """Reconstruct a Project from a serialized dict."""
        from openagents_orchestration.core.state_board import StateBoard

        budget_data = data.get("budget")
        budget = None
        if budget_data:
            # Budget.to_dict() includes derived fields; only pass init args
            budget = Budget(
                token_limit=budget_data.get("token_limit", 50_000),
                token_used=budget_data.get("token_used", 0),
                time_limit_s=budget_data.get("time_limit_s", 300.0),
                max_steps=budget_data.get("max_steps", 20),
            )
            budget.start_time = budget_data.get("start_time", budget.start_time)
            budget.steps_taken = budget_data.get("steps_taken", 0)

        state_board_data = data.get("state_board")
        state_board = None
        if state_board_data:
            state_board = StateBoard.from_dict(
                state_board_data,
                reset_budget_clock=True,
            )

        project = cls(
            objective=data.get("objective", ""),
            project_id=data.get("project_id", ""),
            budget=budget,
            work_dir=Path(data.get("work_dir", ".")),
            state_board=state_board,
            status=ProjectStatus(data.get("status", "pending")),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now(UTC).isoformat())),
            metadata=dict(data.get("metadata", {})),
        )
        return project
