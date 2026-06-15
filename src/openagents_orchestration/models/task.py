"""TaskGraph — explicit task model with DAG support."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from time import time
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    REVIEW = "review"           # 代码已提交，等待 review
    FIX_NEEDED = "fix_needed"   # review 发现问题，需要修复
    WAITING_FOR_HUMAN = "waiting_for_human"  # 等待人类回复/确认


@dataclass
class TaskNode:
    """Single task in the TaskGraph."""

    task_id: str
    description: str
    agent_type: str
    dependencies: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    estimated_complexity: int = 1  # 1–5
    priority: int = 0  # higher = more urgent; Director sorts ready tasks by this
    max_iterations: int = 5  # collaboration mode circuit breaker: max coder↔reviewer rounds
    deadline_s: float = 0.0  # 0 = no deadline; >0 = absolute timestamp after which task is considered stale
    status: TaskStatus = TaskStatus.PENDING
    error: str | None = None
    retry_count: int = 0

    # Collaboration pattern support
    collaboration_pattern: str = "default"
    collaboration_participants: dict[str, str] = field(default_factory=dict)

    # Mutable during execution
    input_context: str = ""
    result_output: str = ""
    actual_artifacts: list[str] = field(default_factory=list)

    # Team support: nested subgraph for sub-delegation
    subgraph: TaskGraph | None = None

    # Iterative execution history (for coder -> review -> fix loops)
    iteration_history: list[dict[str, Any]] = field(default_factory=list)
    assigned_agent: str = ""  # resident_id or agent_id bound to this task

    def is_ready(self, completed_ids: set[str]) -> bool:
        """True when all dependencies are in completed_ids."""
        return set(self.dependencies).issubset(completed_ids)

    def is_terminal(self) -> bool:
        """True when task is in a terminal state."""
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.SKIPPED,
        }

    def record_iteration(self, agent_id: str, action: str, output: str = "", artifacts: list[str] | None = None) -> None:
        """Record one iteration step in the task's execution history."""
        self.iteration_history.append({
            "agent_id": agent_id,
            "action": action,
            "output": output[:500],
            "artifacts": artifacts or [],
            "ts": time(),
        })

    def to_dict(self) -> dict[str, Any]:
        result = {
            "task_id": self.task_id,
            "description": self.description,
            "agent_type": self.agent_type,
            "dependencies": self.dependencies,
            "expected_artifacts": self.expected_artifacts,
            "estimated_complexity": self.estimated_complexity,
            "priority": self.priority,
            "max_iterations": self.max_iterations,
            "deadline_s": self.deadline_s,
            "status": self.status.value,
            "error": self.error,
            "retry_count": self.retry_count,
            "collaboration_pattern": self.collaboration_pattern,
            "collaboration_participants": dict(self.collaboration_participants),
            "input_context": self.input_context,
            "result_output": self.result_output,
            "actual_artifacts": self.actual_artifacts,
            "iteration_history": self.iteration_history,
            "assigned_agent": self.assigned_agent,
        }
        if self.subgraph is not None:
            result["subgraph"] = self.subgraph.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskNode:
        node = cls(
            task_id=data["task_id"],
            description=data["description"],
            agent_type=data["agent_type"],
            dependencies=list(data.get("dependencies", [])),
            expected_artifacts=list(data.get("expected_artifacts", [])),
            estimated_complexity=int(data.get("estimated_complexity", 1)),
            priority=int(data.get("priority", 0)),
            max_iterations=int(data.get("max_iterations", 5)),
            deadline_s=float(data.get("deadline_s", 0.0)),
            status=TaskStatus(data.get("status", "pending")),
            error=data.get("error"),
            retry_count=int(data.get("retry_count", 0)),
            collaboration_pattern=data.get("collaboration_pattern", "default"),
            collaboration_participants=dict(data.get("collaboration_participants", {})),
            input_context=data.get("input_context", ""),
            result_output=data.get("result_output", ""),
            actual_artifacts=list(data.get("actual_artifacts", [])),
        )
        node.iteration_history = list(data.get("iteration_history", []))
        node.assigned_agent = data.get("assigned_agent", "")
        if "subgraph" in data:
            node.subgraph = TaskGraph.from_dict(data["subgraph"])
        return node


@dataclass
class TaskGraph:
    """Collection of tasks with dependency edges."""

    objective: str
    tasks: list[TaskNode]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Check for unknown dependencies and cycles."""
        task_ids = {t.task_id for t in self.tasks}
        for t in self.tasks:
            for dep in t.dependencies:
                if dep not in task_ids:
                    raise ValueError(
                        f"Task '{t.task_id}' depends on unknown task '{dep}'"
                    )
        if self._has_cycle():
            raise ValueError("TaskGraph contains circular dependencies")

    def _has_cycle(self) -> bool:
        """Kahn's algorithm — if not all nodes processed, there is a cycle."""
        in_degree: dict[str, int] = {t.task_id: 0 for t in self.tasks}
        adj: dict[str, list[str]] = {t.task_id: [] for t in self.tasks}
        for t in self.tasks:
            for dep in t.dependencies:
                adj[dep].append(t.task_id)
                in_degree[t.task_id] += 1

        q = deque([nid for nid, deg in in_degree.items() if deg == 0])
        visited = 0
        while q:
            nid = q.popleft()
            visited += 1
            for nxt in adj[nid]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    q.append(nxt)
        return visited != len(self.tasks)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------

    def topological_layers(self) -> list[list[TaskNode]]:
        """Return tasks grouped by dependency layer.

        Layer 0: tasks with no dependencies.
        Layer N: tasks whose dependencies are all in layers < N.
        """
        remaining: dict[str, TaskNode] = {t.task_id: t for t in self.tasks}
        in_degree: dict[str, int] = {
            t.task_id: len(t.dependencies) for t in self.tasks
        }
        adj: dict[str, list[str]] = {t.task_id: [] for t in self.tasks}
        for t in self.tasks:
            for dep in t.dependencies:
                adj[dep].append(t.task_id)

        layers: list[list[TaskNode]] = []
        while remaining:
            ready = [nid for nid, deg in in_degree.items() if deg == 0 and nid in remaining]
            if not ready:
                raise ValueError("Cycle detected (should have been caught by validate)")
            layer_nodes = [remaining.pop(nid) for nid in ready]
            layers.append(layer_nodes)
            for nid in ready:
                for nxt in adj[nid]:
                    in_degree[nxt] -= 1
        return layers

    def get_task(self, task_id: str) -> TaskNode | None:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskGraph:
        return cls(
            objective=data["objective"],
            tasks=[TaskNode.from_dict(t) for t in data.get("tasks", [])],
        )
