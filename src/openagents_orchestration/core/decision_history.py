"""DecisionRecord — bounded history of Director decisions and their outcomes.

Injected into the StateBoard snapshot so the Director LLM can learn from
its own decision history: "I spawned coder-t1, it produced 3 artifacts
and passed tests" or "I spawned coder-t2 with same prompt, it failed
again — time to replan."

Without this, the Director is memoryless across LLM calls — each cycle
starts from raw state without knowing what it already tried.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionRecord:
    """One Director decision and its known outcome."""

    decision_type: str  # spawn_agent | spawn_resident | replan | ask_human | skip
    task_id: str = ""
    agent_id: str = ""
    agent_type: str = ""
    reasoning: str = ""  # brief: why this decision was made
    outcome: str = ""    # completed | failed | stuck | pending | unknown
    artifacts_produced: list[str] = field(default_factory=list)
    error: str = ""
    token_spent: int = 0
    steps_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision_type,
            "task": self.task_id,
            "agent": self.agent_id,
            "type": self.agent_type,
            "why": self.reasoning[:200],
            "outcome": self.outcome,
            "artifacts": self.artifacts_produced[:5],
            "error": self.error[:200],
            "token_spent": self.token_spent,
            "steps_used": self.steps_used,
        }


class DecisionHistory:
    """Bounded ring of Director decisions with outcomes.

    Thread-safe append; intended to be written by Runner after each
    agent completion/failure, and read by StateBoard.snapshot() for
    the Director LLM.
    """

    def __init__(self, max_decisions: int = 50):
        self._decisions: deque[DecisionRecord] = deque(maxlen=max_decisions)
        self._max = max_decisions

    def record(self, decision: DecisionRecord) -> None:
        self._decisions.append(decision)

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the most recent N decisions as dicts for snapshot."""
        items = list(self._decisions)[-n:]
        return [d.to_dict() for d in items]

    def summary(self) -> dict[str, Any]:
        """Aggregated stats: what's been tried and how it turned out."""
        if not self._decisions:
            return {"total": 0}

        by_type: dict[str, dict[str, int]] = {}
        for d in self._decisions:
            bucket = by_type.setdefault(d.decision_type, {"total": 0, "completed": 0, "failed": 0})
            bucket["total"] += 1
            if d.outcome == "completed":
                bucket["completed"] += 1
            elif d.outcome == "failed":
                bucket["failed"] += 1

        total = len(self._decisions)
        completed = sum(1 for d in self._decisions if d.outcome == "completed")
        failed = sum(1 for d in self._decisions if d.outcome == "failed")

        return {
            "total_decisions": total,
            "completed": completed,
            "failed": failed,
            "success_rate": round(completed / max(total, 1), 2),
            "by_type": by_type,
        }

    def __len__(self) -> int:
        return len(self._decisions)
