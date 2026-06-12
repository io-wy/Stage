"""Team — project-scoped sub-execution unit with TeamLeader + Workers.

A Team owns a SubStateBoard scoped to its subgraph, manages resident
workers, enforces channel policy for intra-team communication, and
reports progress upward to the Project / GlobalDirector.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from openagents_orchestration.transport.channel_policy import (
    DEFAULT_TEAM_POLICY,
    ChannelPolicy,
)
from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.core.resident import ResidentAgent
from openagents_orchestration.core.state_board import Budget
from openagents_orchestration.core.sub_state_board import SubStateBoard


class TeamStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class TeamSpec:
    """Specification for creating a Team."""

    name: str
    agent_types: list[str] = field(default_factory=lambda: ["coder", "reviewer"])
    max_workers: int = 5
    channel_policy: ChannelPolicy | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent_types": self.agent_types,
            "max_workers": self.max_workers,
            "channel_policy": (
                self.channel_policy.to_dict() if self.channel_policy else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamSpec:
        policy_data = data.get("channel_policy")
        policy = None
        if policy_data:
            from openagents_orchestration.transport.channel_policy import ChannelPolicy
            policy = ChannelPolicy.from_dict(policy_data)
        return cls(
            name=data.get("name", ""),
            agent_types=list(data.get("agent_types", ["coder", "reviewer"])),
            max_workers=int(data.get("max_workers", 5)),
            channel_policy=policy,
        )


@dataclass
class Team:
    """A team within a Project, managed by a TeamLeader with Workers."""

    team_id: str
    project_id: str
    name: str
    sub_state_board: SubStateBoard
    leader_id: str | None = None
    workers: dict[str, ResidentAgent] = field(default_factory=dict, repr=False)
    channel_policy: ChannelPolicy = field(default_factory=lambda: DEFAULT_TEAM_POLICY)
    status: TeamStatus = TeamStatus.IDLE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Transition to RUNNING."""
        self.status = TeamStatus.RUNNING
        self.sub_state_board.log_event(
            "team.started",
            message=f"Team {self.team_id} ({self.name}) started",
        )

    def pause(self) -> None:
        """Transition to PAUSED."""
        self.status = TeamStatus.PAUSED
        self.sub_state_board.log_event(
            "team.paused",
            message=f"Team {self.team_id} paused",
        )

    def resume(self) -> None:
        """Transition back to RUNNING."""
        self.status = TeamStatus.RUNNING
        self.sub_state_board.log_event(
            "team.resumed",
            message=f"Team {self.team_id} resumed",
        )

    async def stop(self) -> None:
        """Stop all workers and transition to STOPPED."""
        for worker in list(self.workers.values()):
            await worker.stop()
        self.workers.clear()
        self.status = TeamStatus.STOPPED
        self.sub_state_board.log_event(
            "team.stopped",
            message=f"Team {self.team_id} stopped",
        )

    # -- worker management -----------------------------------------------------

    def add_worker(self, worker: ResidentAgent) -> None:
        """Register a worker resident."""
        if len(self.workers) >= 10:
            raise RuntimeError(f"Team {self.team_id} worker limit reached")
        self.workers[worker.resident_id] = worker
        self.sub_state_board.register_resident(worker.state)

    def remove_worker(self, worker_id: str) -> ResidentAgent | None:
        """Remove and return a worker, or None if not found."""
        worker = self.workers.pop(worker_id, None)
        if worker is not None:
            self.sub_state_board.update_resident(worker_id, status="stopped")
        return worker

    def get_worker(self, worker_id: str) -> ResidentAgent | None:
        return self.workers.get(worker_id)

    def list_workers(self, status: str | None = None) -> list[ResidentAgent]:
        """List workers, optionally filtered by status."""
        result = list(self.workers.values())
        if status is not None:
            result = [w for w in result if w.state.status == status]
        return result

    # -- messaging -------------------------------------------------------------

    def can_communicate(self, sender: str, recipient: str) -> bool:
        """Check channel policy for intra-team messaging."""
        return self.channel_policy.allows(sender, recipient)

    async def route_message(self, msg: StructuredMessage) -> bool:
        """Route a message to a team worker if allowed by policy.

        Returns True if the message was accepted (policy allows it).
        """
        sender = msg.header.sender
        recipient = msg.header.recipient

        if not self.can_communicate(sender, recipient):
            self.sub_state_board.log_event(
                "team.policy_blocked",
                message=f"Blocked {sender} -> {recipient}",
            )
            return False

        # Deliver to resident inbox if target is a local worker
        worker = self.workers.get(recipient)
        if worker is not None and getattr(worker, "_active", False):
            await worker.send({
                "task": "",
                "content": msg.text,
                "from": sender,
                "payload": msg.payload,
            })

        # Also store in SubStateBoard mailbox for pull-based retrieval
        await self.sub_state_board.send_structured(msg)
        return True

    # -- snapshot --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "project_id": self.project_id,
            "name": self.name,
            "leader_id": self.leader_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
            "channel_policy": self.channel_policy.to_dict(),
            "worker_ids": list(self.workers.keys()),
            "sub_state_board": self.sub_state_board.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Team:
        from openagents_orchestration.core.state_board import StateBoard

        board_data = data.get("sub_state_board")
        if board_data:
            # Reconstruct via StateBoard.from_dict then wrap
            parent_board = StateBoard.from_dict(board_data, reset_budget_clock=True)
            sub_board = SubStateBoard(
                parent=parent_board,
                objective=parent_board.objective,
                budget=parent_board.budget,
            )
            # Copy tasks and events back
            sub_board.tasks = parent_board.tasks
            sub_board.events = parent_board.events
        else:
            # Fallback: create minimal sub-board (parent will be wired later)
            from openagents_orchestration.core.state_board import StateBoard, Budget
            dummy_parent = StateBoard(
                objective=data.get("name", ""),
                budget=Budget(),
                echo=False,
            )
            sub_board = SubStateBoard(
                parent=dummy_parent,
                objective=data.get("name", ""),
            )

        policy_data = data.get("channel_policy")
        policy = DEFAULT_TEAM_POLICY
        if policy_data:
            from openagents_orchestration.transport.channel_policy import ChannelPolicy
            policy = ChannelPolicy.from_dict(policy_data)

        return cls(
            team_id=data.get("team_id", ""),
            project_id=data.get("project_id", ""),
            name=data.get("name", ""),
            sub_state_board=sub_board,
            leader_id=data.get("leader_id"),
            channel_policy=policy,
            status=TeamStatus(data.get("status", "idle")),
            created_at=datetime.fromisoformat(
                data.get("created_at", datetime.now(UTC).isoformat())
            ),
            metadata=dict(data.get("metadata", {})),
        )
