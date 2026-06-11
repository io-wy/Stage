"""SubStateBoard — child StateBoard scoped to a single Team's subgraph.

Inherits event logging and budget tracking from the parent while maintaining
its own task graph.  Used by TeamLeader so that it sees only its local tasks
but still reports token usage and events upward.
"""

from __future__ import annotations

from typing import Any

from openagents_orchestration.state_board import Budget, StateBoard
from openagents_orchestration.models.task import TaskGraph


class SubStateBoard(StateBoard):
    """StateBoard scoped to a single team."""

    def __init__(
        self,
        parent: StateBoard,
        objective: str,
        budget: Budget | None = None,
    ):
        super().__init__(
            objective=objective,
            budget=budget or Budget(
                token_limit=max(1, parent.budget.token_remaining // 4),
                time_limit_s=max(60.0, parent.budget.time_remaining_s / 4),
                max_steps=30,
            ),
            echo=parent._echo,
        )
        self._parent = parent

    def log_event(self, event_type: str, *, task_id: str | None = None, agent_id: str | None = None, message: str = "", **payload: Any) -> None:
        """Log locally AND bubble up to parent for global observability."""
        super().log_event(
            event_type,
            task_id=task_id,
            agent_id=agent_id,
            message=message,
            **payload,
        )
        # Forward to parent with a sub-board prefix so filtering is easy
        self._parent.log_event(
            f"sub.{event_type}",
            task_id=task_id,
            agent_id=agent_id,
            message=f"[{self.objective}] {message}"[:500],
            **payload,
        )

    def add_tokens(self, n: int) -> None:
        super().add_tokens(n)
        # Also deduct from parent budget so global tracking stays accurate
        self._parent.add_tokens(n)

    def add_steps(self, n: int) -> None:
        super().add_steps(n)
        self._parent.add_steps(n)
