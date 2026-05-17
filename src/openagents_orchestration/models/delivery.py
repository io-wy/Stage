"""Delivery — execution results and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskResult:
    """Outcome of a single task execution."""

    task_id: str = ""
    status: str = "pending"  # completed | failed | skipped
    output: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    token_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "output": self.output,
            "artifacts": self.artifacts,
            "error": self.error,
            "token_used": self.token_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskResult:
        return cls(
            task_id=data.get("task_id", ""),
            status=data.get("status", "pending"),
            output=data.get("output", ""),
            artifacts=list(data.get("artifacts", [])),
            error=data.get("error"),
            token_used=int(data.get("token_used", 0)),
        )


@dataclass
class DeliveryReport:
    """Final report after all phases complete."""

    objective: str = ""
    task_results: list[TaskResult] = field(default_factory=list)
    summary: str = ""
    final_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        total = len(self.task_results)
        if total == 0:
            return 0.0
        completed = sum(1 for r in self.task_results if r.status == "completed")
        return completed / total

    @property
    def all_succeeded(self) -> bool:
        return all(r.status == "completed" for r in self.task_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "task_results": [r.to_dict() for r in self.task_results],
            "summary": self.summary,
            "final_output": self.final_output,
            "metadata": self.metadata,
            "success_rate": self.success_rate,
            "all_succeeded": self.all_succeeded,
        }
