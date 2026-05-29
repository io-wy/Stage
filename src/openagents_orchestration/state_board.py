"""StateBoard — global state panel for the orchestrator.

Tracks tasks, agents, artifacts, budget, and events in a structured form
that can be serialized into an LLM-readable snapshot for decision-making.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openagents_orchestration.models.delivery import DeliveryReport, TaskResult
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STALLED = "stalled"
    DONE = "done"
    FAILED = "failed"


@dataclass
class AgentState:
    """Runtime state of a single agent instance."""

    agent_id: str
    agent_type: str
    status: AgentStatus = AgentStatus.IDLE
    current_task: str | None = None
    output_so_far: str = ""
    files_claimed: list[str] = field(default_factory=list)
    files_verified: list[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    token_used: int = 0
    retry_count: int = 0
    steps_used: int = 0
    consecutive_tool_failures: int = 0
    consecutive_empty_responses: int = 0
    api_error_count: int = 0
    last_artifact_time: float = 0.0
    fallback_attempts: int = 0
    health_status: str = "healthy"  # healthy | warning | critical

    # -- behavioral profiling (populated by SDK event bus bridge) --
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    llm_call_count: int = 0
    total_llm_latency_ms: float = 0.0
    first_artifact_time: float = 0.0
    error_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "current_task": self.current_task,
            "files_claimed": self.files_claimed,
            "files_verified": self.files_verified,
            "elapsed_s": round(time.time() - self.start_time, 1) if self.start_time else 0,
            "token_used": self.token_used,
            "retry_count": self.retry_count,
            "steps_used": self.steps_used,
            "consecutive_tool_failures": self.consecutive_tool_failures,
            "consecutive_empty_responses": self.consecutive_empty_responses,
            "api_error_count": self.api_error_count,
            "fallback_attempts": self.fallback_attempts,
            "health_status": self.health_status,
            "tool_call_counts": self.tool_call_counts,
            "llm_call_count": self.llm_call_count,
            "avg_llm_latency_ms": round(self.total_llm_latency_ms / max(self.llm_call_count, 1), 1) if self.llm_call_count else 0,
            "first_artifact_time": self.first_artifact_time,
        }


@dataclass
class ArtifactRecord:
    """Record of a file artifact produced by an agent."""

    path: str
    status: str = "claimed"  # claimed | verified | missing | conflict
    claimed_by: str = ""
    verified_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "claimed_by": self.claimed_by,
        }


@dataclass
class Budget:
    """Shared budget across the entire orchestration."""

    token_limit: int = 50_000
    token_used: int = 0
    time_limit_s: float = 300.0
    start_time: float = field(default_factory=time.time)
    max_steps: int = 20
    steps_taken: int = 0

    @property
    def token_remaining(self) -> int:
        return max(0, self.token_limit - self.token_used)

    @property
    def time_remaining_s(self) -> float:
        return max(0.0, self.time_limit_s - (time.time() - self.start_time))

    @property
    def exhausted(self) -> bool:
        return self.token_remaining <= 0 or self.time_remaining_s <= 0 or self.steps_taken >= self.max_steps

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_used": self.token_used,
            "token_limit": self.token_limit,
            "token_remaining": self.token_remaining,
            "time_remaining_s": round(self.time_remaining_s, 1),
            "steps_taken": self.steps_taken,
            "max_steps": self.max_steps,
            "exhausted": self.exhausted,
        }


@dataclass
class Event:
    """A single event in the orchestration timeline."""

    ts: float
    event_type: str
    task_id: str | None = None
    agent_id: str | None = None
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class StateBoard:
    """Global state panel — the single source of truth for the Director."""

    def __init__(
        self,
        objective: str,
        budget: Budget | None = None,
        *,
        echo: bool = True,
        recorder: Any = None,
        snapshotter: Any = None,
    ):
        self.objective = objective
        self.tasks: dict[str, TaskNode] = {}
        self.agents: dict[str, AgentState] = {}
        self.residents: dict[str, Any] = {}
        self.artifacts: dict[str, ArtifactRecord] = {}
        self.budget = budget or Budget()
        self.events: list[Event] = []
        self._final_summary: str = ""
        self._echo = echo
        self._human_questions: list[dict[str, Any]] = []
        self._pending_messages: list[dict[str, Any]] = []
        self._recorder = recorder
        self._snapshotter = snapshotter
        self._observers: list[Any] = []  # event bus subscribers

    # -- task management -----------------------------------------------------

    def add_tasks(self, graph: TaskGraph) -> None:
        """Import tasks from a TaskGraph."""
        for task in graph.tasks:
            self.tasks[task.task_id] = task
        self.log_event(
            "tasks.imported",
            message=f"Imported {len(graph.tasks)} task(s) from graph",
        )

    def add_task(self, task: TaskNode) -> None:
        self.tasks[task.task_id] = task
        self.log_event("task.added", task_id=task.task_id, message=f"Added {task.agent_type} task: {task.description[:80]}")

    def update_task(self, task_id: str, **fields: Any) -> None:
        if task_id not in self.tasks:
            return
        task = self.tasks[task_id]
        changed: list[str] = []
        for key, value in fields.items():
            if hasattr(task, key):
                old = getattr(task, key)
                # Coerce string status to TaskStatus enum
                if key == "status" and isinstance(value, str):
                    from openagents_orchestration.models.task import TaskStatus
                    try:
                        value = TaskStatus(value)
                    except ValueError:
                        pass
                if old != value:
                    changed.append(f"{key}={value}")
                setattr(task, key, value)
        if changed:
            status = fields.get("status", task.status)
            status_val = status.value if hasattr(status, "value") else status
            # Serialize fields for event replay (resume)
            serializable_fields = {}
            for k, v in fields.items():
                if hasattr(v, "value"):
                    serializable_fields[k] = v.value
                else:
                    serializable_fields[k] = v
            self.log_event(
                f"task.{status_val}",
                task_id=task_id,
                message=", ".join(changed),
                fields=serializable_fields,
            )

    def get_task(self, task_id: str) -> TaskNode | None:
        return self.tasks.get(task_id)

    def list_tasks(self, status: TaskStatus | None = None) -> list[TaskNode]:
        if status is None:
            return list(self.tasks.values())
        return [t for t in self.tasks.values() if t.status == status]

    def tasks_ready(self) -> list[TaskNode]:
        """Tasks whose dependencies are all completed."""
        completed = {t.task_id for t in self.tasks.values() if t.status == TaskStatus.COMPLETED}
        return [
            t for t in self.tasks.values()
            if t.status == TaskStatus.PENDING and t.is_ready(completed)
        ]

    def tasks_blocked(self) -> list[TaskNode]:
        """Tasks whose dependencies have failed."""
        failed = {t.task_id for t in self.tasks.values() if t.status == TaskStatus.FAILED}
        return [
            t for t in self.tasks.values()
            if t.status == TaskStatus.PENDING and set(t.dependencies) & failed
        ]

    # -- agent management ----------------------------------------------------

    def register_agent(self, agent_id: str, agent_type: str) -> None:
        if agent_id not in self.agents:
            self.agents[agent_id] = AgentState(agent_id=agent_id, agent_type=agent_type)
            self.log_event("agent.registered", agent_id=agent_id, message=f"Registered {agent_type}")

    def update_agent(self, agent_id: str, **fields: Any) -> None:
        if agent_id not in self.agents:
            return
        agent = self.agents[agent_id]
        changed: list[str] = []
        for key, value in fields.items():
            if hasattr(agent, key):
                old = getattr(agent, key)
                # Coerce string status to AgentStatus enum
                if key == "status" and isinstance(value, str):
                    try:
                        value = AgentStatus(value)
                    except ValueError:
                        pass
                if old != value:
                    changed.append(f"{key}={value}")
                setattr(agent, key, value)
        if changed:
            status = fields.get("status", agent.status)
            status_val = status.value if hasattr(status, "value") else status
            serializable_fields = {}
            for k, v in fields.items():
                if hasattr(v, "value"):
                    serializable_fields[k] = v.value
                else:
                    serializable_fields[k] = v
            self.log_event(
                f"agent.{status_val}",
                agent_id=agent_id,
                message=", ".join(changed),
                fields=serializable_fields,
            )

    def get_agent(self, agent_id: str) -> AgentState | None:
        return self.agents.get(agent_id)

    # -- resident management -------------------------------------------------

    def register_resident(self, state: Any) -> None:
        self.residents[state.resident_id] = state
        self.log_event(
            "resident.registered",
            agent_id=state.resident_id,
            message=f"Registered {state.agent_type} resident",
        )

    def update_resident(self, resident_id: str, **fields: Any) -> None:
        if resident_id not in self.residents:
            return
        resident = self.residents[resident_id]
        changed: list[str] = []
        for key, value in fields.items():
            if hasattr(resident, key):
                old = getattr(resident, key)
                if old != value:
                    changed.append(f"{key}={value}")
                setattr(resident, key, value)
        if changed:
            status = fields.get("status", resident.status)
            serializable_fields = {}
            for k, v in fields.items():
                if hasattr(v, "value"):
                    serializable_fields[k] = v.value
                else:
                    serializable_fields[k] = v
            self.log_event(
                f"resident.{status}",
                agent_id=resident_id,
                message=", ".join(changed),
                fields=serializable_fields,
            )

    def get_resident(self, resident_id: str) -> Any | None:
        return self.residents.get(resident_id)

    def list_residents(self, agent_type: str | None = None) -> list[Any]:
        if agent_type is None:
            return list(self.residents.values())
        return [r for r in self.residents.values() if r.agent_type == agent_type]

    # -- artifact management -------------------------------------------------

    def claim_artifact(self, task_id: str, paths: list[str]) -> None:
        for path in paths:
            self.artifacts[path] = ArtifactRecord(
                path=path,
                status="claimed",
                claimed_by=task_id,
            )
        if paths:
            self.log_event(
                "artifact.claimed",
                message=f"{len(paths)} artifact(s) by {task_id}: {paths}",
                paths=paths,
            )

    def verify_artifact(self, path: str, exists: bool = True) -> None:
        rec = self.artifacts.get(path)
        if rec is None:
            self.artifacts[path] = ArtifactRecord(path=path, status="verified" if exists else "missing")
            return
        rec.status = "verified" if exists else "missing"
        rec.verified_at = time.time()
        self.log_event(f"artifact.{rec.status}", message=path)

    # -- event log -----------------------------------------------------------

    def log_event(self, event_type: str, *, task_id: str | None = None, agent_id: str | None = None, message: str = "", **payload: Any) -> None:
        evt = Event(
            ts=time.time(),
            event_type=event_type,
            task_id=task_id,
            agent_id=agent_id,
            message=message,
            payload=dict(payload),
        )
        self.events.append(evt)
        # Notify observers (fire-and-forget, errors must not propagate)
        for obs in self._observers:
            try:
                obs(evt)
            except Exception:
                pass
        # Persist to JSONL if recorder is attached
        if self._recorder is not None:
            self._recorder.append(
                event_type,
                task_id=task_id,
                agent_id=agent_id,
                message=message,
                **payload,
            )
        if self._echo:
            ts_str = time.strftime("%H:%M:%S", time.localtime(evt.ts))
            parts = [f"[{ts_str}]"]
            if agent_id:
                parts.append(f"[{agent_id}]")
            parts.append(event_type)
            if task_id:
                parts.append(f"task={task_id}")
            if message:
                parts.append(f"— {message}")
            print(" ".join(parts), file=sys.stderr, flush=True)
        # Trigger snapshot if threshold reached
        self._maybe_snapshot()

    def _maybe_snapshot(self) -> None:
        """Trigger periodic snapshot if snapshotter is attached."""
        if self._snapshotter is not None:
            self._snapshotter.on_mutation(self)

    def format_events(self) -> str:
        """Return a human-readable event timeline."""
        if not self.events:
            return "(no events)"
        lines: list[str] = []
        start_ts = self.events[0].ts
        for evt in self.events:
            rel_s = round(evt.ts - start_ts, 1)
            parts = [f"  +{rel_s:>6.1f}s"]
            parts.append(f"{evt.event_type:<20}")
            if evt.agent_id:
                parts.append(f"agent={evt.agent_id}")
            if evt.task_id:
                parts.append(f"task={evt.task_id}")
            if evt.message:
                parts.append(f"— {evt.message}")
            lines.append("  ".join(parts))
        return "\n".join(lines)

    # -- budget --------------------------------------------------------------

    def add_tokens(self, n: int) -> None:
        before = self.budget.token_used
        self.budget.token_used += n
        if n > 0:
            self.log_event("budget.tokens", message=f"+{n} (was {before}, now {self.budget.token_used})", n=n)

    def add_steps(self, n: int) -> None:
        before = self.budget.steps_taken
        self.budget.steps_taken += n
        if n > 0:
            self.log_event("budget.steps", message=f"+{n} (was {before}, now {self.budget.steps_taken})", n=n)

    def increment_step(self) -> None:
        self.budget.steps_taken += 1

    # -- fallback suggestion -------------------------------------------------

    def suggest_fallback(self, task_id: str) -> str:
        """基于状态生成一段给导演看的建议 prompt，不做最终决定。"""
        task = self.get_task(task_id)
        if task is None:
            return ""

        agent_id = f"{task.agent_type}-{task_id}"
        agent = self.get_agent(agent_id)

        parts: list[str] = []

        # 观察 1: 失败次数
        if agent and agent.fallback_attempts >= 2:
            parts.append(
                f"任务 {task_id} 已连续失败 {agent.fallback_attempts} 次，"
                f"建议优先考虑 ask_human 或跳过该任务。"
            )

        # 观察 2: 资源耗尽情况
        if agent and agent.steps_used >= 25:
            arts = task.actual_artifacts or task.expected_artifacts or []
            if arts:
                parts.append(
                    f"Agent 在 {agent.steps_used} 步中产出了 {len(arts)} 个 artifact，"
                    f"但 step 耗尽未完成。任务可能过大，建议 replan 拆分。"
                )
            else:
                parts.append(
                    f"Agent 用了 {agent.steps_used} 步但几乎没有产出，"
                    f"可能在原地打转，建议尝试 spawn resident。"
                )

        # 观察 3: token 爆表
        if agent and agent.token_used > 40000:
            parts.append(
                f"Token 消耗较高（{agent.token_used}），"
                f"继续用同类型 agent 可能效率不高。"
            )

        # 观察 4: 预算紧张
        if self.budget.time_remaining_s < 120:
            parts.append(
                f"预算紧张（剩余时间 {self.budget.time_remaining_s:.0f}s），"
                f"建议尽快 finalize 或 ask_human。"
            )

        # 观察 5: 错误类型建议
        if task.error:
            if "recommendation: replan" in task.error:
                parts.append(
                    "spawn_agent 建议 replan：可能是任务规格或路径问题。"
                )
            elif "recommendation: retry" in task.error:
                parts.append(
                    "spawn_agent 建议 retry：可能是瞬时错误，但已重试 3 次仍未成功。"
                )
            elif "recommendation: spawn resident" in task.error:
                parts.append(
                    "spawn_agent 建议 spawn resident：agent 可能陷入循环。"
                )
            elif "recommendation: ask_human" in task.error:
                parts.append(
                    "spawn_agent 建议 ask_human：可能需要外部信息或权限。"
                )

        if not parts:
            return ""

        return (
            f"\n[StateBoard 观察 - 任务 {task_id}]\n"
            + "\n".join(f"- {p}" for p in parts)
            + "\n注意：以上是基于状态的观察供你参考，最终决定权在你。"
        )

    # -- mailbox API ---------------------------------------------------------

    def send_mail(self, from_id: str, to_id: str, content: str) -> None:
        """Send a message from one agent to another (or '* broadcast)."""
        self._pending_messages.append({
            "from": from_id,
            "to": to_id,
            "content": content,
            "ts": time.time(),
        })
        self.log_event(
            "mail.sent",
            agent_id=from_id,
            message=f"To {to_id}: {content[:100]}",
            to_id=to_id,
            content=content,
        )

    def messages_for(self, recipient: str) -> list[dict[str, Any]]:
        """Return messages addressed to this recipient (or '*')."""
        return [
            m for m in self._pending_messages
            if m.get("to") in (recipient, "*")
        ]

    def clear_mail(self, recipient: str | None = None) -> int:
        """Clear messages. If recipient given, only clear theirs. Returns count cleared."""
        before = len(self._pending_messages)
        if recipient is None:
            self._pending_messages = []
        else:
            self._pending_messages = [
                m for m in self._pending_messages
                if m.get("to") not in (recipient, "*")
            ]
        cleared = before - len(self._pending_messages)
        if cleared > 0:
            self.log_event(
                "mail.cleared",
                message=f"Cleared {cleared} message(s) for {recipient or 'all'}",
            )
        return cleared

    # -- human questions -----------------------------------------------------

    def ask_human(self, question: str, *, options: str = "", from_agent: str = "") -> str:
        """Record a question for human input. Returns a question ID."""
        qid = f"hq-{len(self._human_questions)}"
        self._human_questions.append({
            "id": qid,
            "from": from_agent,
            "question": question,
            "options": options,
            "answer": None,
        })
        self.log_event(
            "human.asked",
            agent_id=from_agent,
            message=f"{qid}: {question[:100]}",
            question=question,
            options=options,
            from_agent=from_agent,
        )
        return qid

    def reply_human(self, qid: str, answer: str) -> bool:
        """Record a human reply. Returns True if the question was found and unanswered."""
        for entry in self._human_questions:
            if entry["id"] == qid and entry["answer"] is None:
                entry["answer"] = answer
                # Send reply to director via mailbox
                self.send_mail("human", "director", f"[回复 {qid}] {answer}")
                self.log_event(
                    "human.replied",
                    message=f"{qid}: {answer[:100]}",
                    qid=qid,
                    answer=answer,
                )
                return True
        return False

    def get_human_questions(self, answered: bool | None = None) -> list[dict[str, Any]]:
        """Return human questions. answered=None returns all."""
        if answered is None:
            return list(self._human_questions)
        return [q for q in self._human_questions if (q["answer"] is not None) == answered]

    # -- queries for Director ------------------------------------------------

    def has_actionable(self) -> bool:
        """True if there are pending/running tasks or the Director should take another step."""
        if self.budget.exhausted:
            return False
        if self._final_summary:
            return False
        pending = any(t.status == TaskStatus.PENDING for t in self.tasks.values())
        running = any(t.status == TaskStatus.RUNNING for t in self.tasks.values())
        return pending or running

    def all_terminal(self) -> bool:
        """True when every task is in a terminal state."""
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}
        return all(t.status in terminal for t in self.tasks.values())

    def needs_human(self) -> list[str]:
        """Task IDs that failed and need manual intervention."""
        return [
            t.task_id for t in self.tasks.values()
            if t.status == TaskStatus.FAILED
        ]

    def progress_summary(self) -> dict[str, Any]:
        """Compact execution progress summary for the Director."""
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
        skipped = sum(1 for t in self.tasks.values() if t.status == TaskStatus.SKIPPED)
        running = [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
        ready = self.tasks_ready()
        blocked = self.tasks_blocked()
        terminal = completed + failed + skipped
        unanswered = [q for q in self._human_questions if q["answer"] is None]
        return {
            "total_tasks": len(self.tasks),
            "completed_tasks": completed,
            "failed_tasks": failed,
            "skipped_tasks": skipped,
            "running_tasks": len(running),
            "ready_tasks": len(ready),
            "blocked_tasks": len(blocked),
            "terminal_tasks": terminal,
            "all_done": self.all_terminal(),
            "waiting_for_human": len(unanswered),
        }

    # -- full state for persistence -----------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Full state dict for persistence / resume.

        Includes everything needed to reconstruct this StateBoard.
        """
        from openagents_orchestration.models.task import TaskGraph

        return {
            "objective": self.objective,
            "budget": self.budget.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "agents": {aid: a.to_dict() for aid, a in self.agents.items()},
            "residents": {rid: r.to_dict() for rid, r in self.residents.items()},
            "artifacts": {path: a.to_dict() for path, a in self.artifacts.items()},
            "events": [
                {
                    "ts": e.ts,
                    "type": e.event_type,
                    "task_id": e.task_id,
                    "agent_id": e.agent_id,
                    "message": e.message,
                    "payload": e.payload,
                }
                for e in self.events
            ],
            "human_questions": list(self._human_questions),
            "pending_messages": list(self._pending_messages),
            "final_summary": self._final_summary,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        echo: bool = True,
        recorder: Any = None,
        snapshotter: Any = None,
        reset_budget_clock: bool = True,
    ) -> StateBoard:
        """Reconstruct a StateBoard from a full state dict."""
        from openagents_orchestration.models.task import TaskNode, TaskStatus

        objective = data.get("objective", "")
        budget_data = data.get("budget", {})
        budget = Budget(
            token_limit=budget_data.get("token_limit", 50_000),
            token_used=budget_data.get("token_used", 0),
            time_limit_s=budget_data.get("time_limit_s", 300.0),
            max_steps=budget_data.get("max_steps", 20),
        )
        # Restore mutable budget fields from data
        if reset_budget_clock:
            # Resume: reset start_time so budget time is not immediately exhausted
            budget.start_time = time.time()
        else:
            budget.start_time = budget_data.get("start_time", time.time())
        budget.steps_taken = budget_data.get("steps_taken", 0)

        board = cls(
            objective=objective,
            budget=budget,
            echo=echo,
            recorder=recorder,
            snapshotter=snapshotter,
        )

        # Restore tasks
        for tdata in data.get("tasks", []):
            task = TaskNode.from_dict(tdata)
            board.tasks[task.task_id] = task

        # Restore agents
        for aid, adata in data.get("agents", {}).items():
            agent = AgentState(
                agent_id=adata.get("agent_id", aid),
                agent_type=adata.get("agent_type", "coder"),
                status=AgentStatus(adata.get("status", "idle")),
                current_task=adata.get("current_task"),
                output_so_far=adata.get("output_so_far", ""),
                files_claimed=list(adata.get("files_claimed", [])),
                files_verified=list(adata.get("files_verified", [])),
                start_time=adata.get("start_time", 0.0),
                end_time=adata.get("end_time", 0.0),
                token_used=adata.get("token_used", 0),
                retry_count=adata.get("retry_count", 0),
                steps_used=adata.get("steps_used", 0),
                consecutive_tool_failures=adata.get("consecutive_tool_failures", 0),
                consecutive_empty_responses=adata.get("consecutive_empty_responses", 0),
                api_error_count=adata.get("api_error_count", 0),
                last_artifact_time=adata.get("last_artifact_time", 0.0),
                fallback_attempts=adata.get("fallback_attempts", 0),
                health_status=adata.get("health_status", "healthy"),
                tool_call_counts=dict(adata.get("tool_call_counts", {})),
                llm_call_count=adata.get("llm_call_count", 0),
                total_llm_latency_ms=adata.get("total_llm_latency_ms", 0.0),
                first_artifact_time=adata.get("first_artifact_time", 0.0),
                error_types=list(adata.get("error_types", [])),
            )
            board.agents[aid] = agent

        # Restore artifacts
        for path, adata in data.get("artifacts", {}).items():
            board.artifacts[path] = ArtifactRecord(
                path=adata.get("path", path),
                status=adata.get("status", "claimed"),
                claimed_by=adata.get("claimed_by", ""),
                verified_at=adata.get("verified_at", 0.0),
            )

        # Restore events
        for edata in data.get("events", []):
            board.events.append(Event(
                ts=edata.get("ts", 0.0),
                event_type=edata.get("type", ""),
                task_id=edata.get("task_id"),
                agent_id=edata.get("agent_id"),
                message=edata.get("message", ""),
                payload=edata.get("payload", {}),
            ))

        # Restore human questions and messages
        board._human_questions = list(data.get("human_questions", []))
        board._pending_messages = list(data.get("pending_messages", []))
        board._final_summary = data.get("final_summary", "")

        return board

    # -- snapshot for LLM ----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Structured snapshot for the Director LLM to read.

        Designed to be compact but complete — the LLM needs enough context
        to decide: spawn / replan / intervene / skip / finalize.
        """
        # Task summary
        task_lines: list[dict[str, Any]] = []
        for t in self.tasks.values():
            task_lines.append({
                "id": t.task_id,
                "desc": t.description,
                "agent": t.agent_type,
                "status": t.status.value,
                "deps": t.dependencies,
                "artifacts": t.actual_artifacts or t.expected_artifacts,
                "error": t.error,
            })

        # Agent summary
        agent_lines = [a.to_dict() for a in self.agents.values()]

        # Artifact summary
        artifact_lines = [
            {"path": a.path, "status": a.status, "by": a.claimed_by}
            for a in self.artifacts.values()
        ]

        # Resident summary
        resident_lines = [r.to_dict() for r in self.residents.values()]

        # Recent events (last 20)
        recent_events = [
            {"type": e.event_type, "msg": e.message}
            for e in self.events[-20:]
        ]

        # Key signals
        ready = self.tasks_ready()
        blocked = self.tasks_blocked()
        running = [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
        unanswered = [q for q in self._human_questions if q["answer"] is None]

        return {
            "objective": self.objective,
            "budget": self.budget.to_dict(),
            "progress": self.progress_summary(),
            "tasks": task_lines,
            "agents": agent_lines,
            "residents": resident_lines,
            "artifacts": artifact_lines,
            "signals": {
                "ready_to_run": [t.task_id for t in ready],
                "blocked": [t.task_id for t in blocked],
                "running": [t.task_id for t in running],
                "needs_human": self.needs_human(),
                "pending_messages": len(self._pending_messages),
                "unanswered_human_questions": len(unanswered),
                "waiting_for_human": [
                    {"id": q["id"], "question": q["question"][:100]}
                    for q in unanswered
                ],
                "all_done": self.all_terminal(),
            },
            "recent_events": recent_events,
        }

    # -- report assembly -----------------------------------------------------

    def to_report(self) -> DeliveryReport:
        """Build a DeliveryReport from the current board state."""
        task_results = []
        for t in self.tasks.values():
            task_results.append(TaskResult(
                task_id=t.task_id,
                status=t.status.value,
                output=t.result_output,
                artifacts=t.actual_artifacts or t.expected_artifacts,
                error=t.error,
            ))

        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
        skipped = sum(1 for t in self.tasks.values() if t.status == TaskStatus.SKIPPED)

        summary = (
            f"Tasks: {total} total, {completed} completed, "
            f"{failed} failed, {skipped} skipped. "
            f"Token used: {self.budget.token_used}/{self.budget.token_limit}."
        )
        if self.needs_human():
            summary += f" Needs human: {', '.join(self.needs_human())}."

        return DeliveryReport(
            objective=self.objective,
            task_results=task_results,
            summary=summary,
            final_output=self._final_summary,
            metadata={
                "budget": self.budget.to_dict(),
                "progress": self.progress_summary(),
                "agents": {aid: a.to_dict() for aid, a in self.agents.items()},
                "residents": {rid: r.to_dict() for rid, r in self.residents.items()},
            },
        )
