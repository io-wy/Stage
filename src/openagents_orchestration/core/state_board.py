"""StateBoard — global state panel for the orchestrator.

Tracks tasks, agents, artifacts, budget, and events in a structured form
that can be serialized into an LLM-readable snapshot for decision-making.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openagents_orchestration.transport.channel_policy import (
    DEFAULT_GLOBAL_POLICY,
    ChannelPolicy,
    ChannelPolicyError,
)
from openagents_orchestration.core.decision_history import DecisionHistory, DecisionRecord
from openagents_orchestration.core.task_state_machine import TaskStateMachine
from openagents_orchestration.enterprise.human_channel import HumanChannel
from openagents_orchestration.mailbox.base import Mailbox
from openagents_orchestration.mailbox.memory import InMemoryMailbox
from openagents_orchestration.models.delivery import DeliveryReport, TaskResult
from openagents_orchestration.models.message import MessageHeader, StructuredMessage
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.models.trace import TraceContext
from openagents_orchestration.transport.routing import RoutingTable, TopologyType

# Module-level thread pool singleton for _run_sync bridge calls.
_sync_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sb-sync")


class AgentStatus(StrEnum):
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
        if self.token_limit < 0:
            return 2**63 - 1
        return max(0, self.token_limit - self.token_used)

    @property
    def time_remaining_s(self) -> float:
        if self.time_limit_s < 0:
            return 2**63 - 1.0
        return max(0.0, self.time_limit_s - (time.time() - self.start_time))

    @property
    def exhausted(self) -> bool:
        token_exhausted = self.token_limit >= 0 and self.token_used >= self.token_limit
        time_exhausted = self.time_limit_s >= 0 and self.time_remaining_s <= 0
        steps_exhausted = self.max_steps >= 0 and self.steps_taken >= self.max_steps
        return token_exhausted or time_exhausted or steps_exhausted

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_used": self.token_used,
            "token_limit": self.token_limit,
            "token_remaining": self.token_remaining,
            "time_limit_s": self.time_limit_s,
            "start_time": self.start_time,
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
    """Global state panel — shared project context for all agents.

    Evolved from Director-exclusive whiteboard to collaborative workspace.
    All agents read/write project state directly; Director only intervenes
    on exceptions or global resource decisions.
    """

    def __init__(
        self,
        objective: str,
        budget: Budget | None = None,
        *,
        echo: bool = True,
        recorder: Any = None,
        snapshotter: Any = None,
        mailbox_backend: str = "memory",
        redis_url: str | None = None,
        channel_policy: ChannelPolicy | None = None,
        human_channel: HumanChannel | None = None,
        project_id: str = "",
        team_id: str = "",
        max_events: int = 10_000,
        snapshot_interval_s: float = 5.0,
    ):
        self.objective = objective
        self.project_id = project_id
        self.team_id = team_id
        self.tasks: dict[str, TaskNode] = {}
        self.agents: dict[str, AgentState] = {}
        self.residents: dict[str, Any] = {}
        self.artifacts: dict[str, ArtifactRecord] = {}
        self.budget = budget or Budget()
        self.events: list[Event] = []
        self._max_events = max_events
        self._final_summary: str = ""
        self._echo = echo
        self._human_channel = human_channel or HumanChannel()
        self._recorder = recorder
        self._snapshotter = snapshotter
        self._last_snapshot_ts: float = 0.0
        self._snapshot_interval_s = snapshot_interval_s
        self._observers: list[Any] = []  # event bus subscribers

        # Mailbox system (v2)
        self._mailbox_backend = mailbox_backend
        self._redis_client: Any = None
        self._mailboxes: dict[str, Mailbox] = {}
        if mailbox_backend == "redis" and redis_url:
            self._init_redis(redis_url)

        # Mailbox system helpers (added after init so mypy sees them)
        self._mailbox_cls = InMemoryMailbox

        # Channel policy
        self._channel_policy = channel_policy or DEFAULT_GLOBAL_POLICY

        # Tracing (P4)
        self._traces: dict[str, TraceContext] = {}

        # Routing (P5)
        self._routing_table = RoutingTable()

        # Change tracking for incremental snapshot diff
        self._previous_task_status: dict[str, str] = {}

        self.decision_history = DecisionHistory()
        self.project_context: dict[str, Any] = {
            "test_reports": [],      # latest pytest output per module
            "error_logs": [],        # structured errors from agent runs
            "code_changes": [],      # diff-like summary of recent edits
            "shared_notes": {},      # key findings agents want to persist
        }
        self._mail_sent_callbacks: list[Any] = []  # collaboration loop wake hooks

    def on_mail_sent(self, callback: Any) -> None:
        """Register a callback invoked on every ``send_structured``.

        Called as ``callback()`` — no arguments. Used by the collaboration
        loop to wake when a resident sends a signal via mailbox.
        """
        self._mail_sent_callbacks.append(callback)

    # -- mailbox v2 helpers --------------------------------------------------

    def _init_redis(self, redis_url: str) -> None:
        """Attempt to connect to Redis; fall back to memory on failure."""
        try:
            import redis.asyncio as aioredis
            self._redis_client = aioredis.from_url(redis_url, decode_responses=True)
            self._mailbox_cls = None
        except (ImportError, ValueError, OSError, ConnectionError) as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Redis init failed for %s (%s: %s), falling back to memory",
                redis_url, type(exc).__name__, exc,
            )
            self._mailbox_backend = "memory"
            self._redis_client = None

    async def validate_redis(self) -> bool:
        """Ping Redis if configured; downgrade to memory on failure."""
        if self._mailbox_backend != "redis" or self._redis_client is None:
            return True
        try:
            await self._redis_client.ping()
            return True
        except (OSError, ConnectionError, TimeoutError) as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Redis unreachable (%s: %s), falling back to in-memory mailbox",
                type(exc).__name__, exc,
            )
            self._mailbox_backend = "memory"
            self._redis_client = None
            return False

    def _get_or_create_mailbox(self, agent_id: str) -> Mailbox:
        """Return the mailbox for an agent, creating it if necessary."""
        if agent_id not in self._mailboxes:
            if self._mailbox_backend == "redis" and self._redis_client is not None:
                from openagents_orchestration.mailbox.redis import RedisMailbox
                self._mailboxes[agent_id] = RedisMailbox(
                    self._redis_client, agent_id
                )
            else:
                self._mailboxes[agent_id] = InMemoryMailbox()
        return self._mailboxes[agent_id]

    # -- tracing helpers -----------------------------------------------------

    def start_trace(self, key: str, *, parent: TraceContext | None = None) -> TraceContext:
        """Start (or replace) a trace context for a task/agent/message flow."""
        trace = parent.child() if parent else TraceContext()
        self._traces[key] = trace
        self.log_event(
            "trace.started",
            task_id=key if key in self.tasks else None,
            agent_id=key if key in self.agents else None,
            message=f"trace_id={trace.trace_id}",
            trace_id=trace.trace_id,
        )
        return trace

    def get_trace(self, key: str) -> TraceContext | None:
        """Return the active trace context for a key, if any."""
        return self._traces.get(key)

    def propagate_trace(self, msg: StructuredMessage, *, key: str | None = None) -> StructuredMessage:
        """Inject an active trace context into a message.

        Priority:
        1. Use ``key`` if provided and a trace exists for it.
        2. Use the sender's active trace.
        3. Use the recipient's active trace.
        4. Leave the message's own trace_id unchanged.
        """
        if msg.header.trace_id:
            return msg
        for candidate in (key, msg.header.sender, msg.header.recipient):
            if candidate and candidate in self._traces:
                self._traces[candidate].inject_into_message(msg)
                return msg
        return msg

    # -- routing helpers -----------------------------------------------------

    def subscribe_topic(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a pubsub topic."""
        self._routing_table.subscribe(agent_id, topic)
        self.log_event(
            "routing.subscribe",
            agent_id=agent_id,
            message=f"topic={topic}",
            topic=topic,
        )

    def unsubscribe_topic(self, agent_id: str, topic: str) -> None:
        """Unsubscribe an agent from a pubsub topic."""
        self._routing_table.unsubscribe(agent_id, topic)

    def add_route(self, pattern: str, topology: str, *, priority_boost: int = 0) -> None:
        """Add a routing rule (e.g. pattern='type:reviewer', topology='broadcast')."""
        from openagents_orchestration.transport.routing import RouteEntry
        self._routing_table.add_route(
            RouteEntry(pattern=pattern, topology=topology, priority_boost=priority_boost)
        )

    # -- DLQ inspection ------------------------------------------------------

    async def inspect_dlq(self) -> dict[str, dict[str, Any]]:
        """Return DLQ summary per agent.  Called by the Director each cycle."""
        result: dict[str, dict[str, Any]] = {}
        for agent_id, mbox in self._mailboxes.items():
            size = await mbox.dlq_size()
            if size:
                result[agent_id] = {
                    "size": size,
                    "latest": await mbox.dlq_peek(limit=3),
                }
        return result

    def aggregate_mailbox_metrics(self) -> dict[str, Any]:
        """Return aggregated mailbox metrics across all agents.

        Provides a global view of the messaging subsystem: total enqueued,
        dequeued, acked, nacked, DLQ size, and circuit-breaker state.
        Useful for monitoring, alerting, and Prometheus export.
        """
        total = {
            "mailbox_count": 0,
            "total_enqueued": 0,
            "total_dequeued": 0,
            "total_acked": 0,
            "total_nacked": 0,
            "total_enqueue_rejected": 0,
            "total_expired": 0,
            "total_dlq_size": 0,
            "total_dlq_moved": 0,
            "total_dlq_replayed": 0,
            "per_agent": {},
        }
        for agent_id, mbox in self._mailboxes.items():
            total["mailbox_count"] += 1
            metrics = getattr(mbox, "metrics", None)
            if metrics is not None:
                snap = metrics.snapshot()
                total["total_enqueued"] += snap["enqueued"]
                total["total_dequeued"] += snap["dequeued"]
                total["total_acked"] += snap["acked"]
                total["total_nacked"] += snap["nacked"]
                total["total_enqueue_rejected"] += snap["enqueue_rejected"]
                total["total_expired"] += snap["expired"]
                total["total_dlq_moved"] += snap["dlq_moved"]
                total["total_dlq_replayed"] += snap["dlq_replayed"]
                total["per_agent"][agent_id] = snap
            # Circuit breaker state (InMemoryMailbox only)
            cb_state = getattr(mbox, "_circuit_state", None)
            if cb_state and cb_state != "closed":
                total["per_agent"][agent_id] = total["per_agent"].get(agent_id, {})
                total["per_agent"][agent_id]["circuit_state"] = cb_state
        return total

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
            self.log_event(
                "task.update_unknown",
                task_id=task_id,
                message=f"Attempted to update unknown task: {task_id}",
            )
            return
        task = self.tasks[task_id]
        force: bool = fields.pop("_force", False)  # internal: skip validation for state restore
        changed: list[str] = []
        for key, value in fields.items():
            if hasattr(task, key):
                old = getattr(task, key)
                # Coerce string status to TaskStatus enum
                if key == "status" and isinstance(value, str):
                    with contextlib.suppress(ValueError):
                        value = TaskStatus(value)
                # Validate state transitions (skip during state restoration)
                if not force and key == "status" and hasattr(value, "value"):
                    result = TaskStateMachine.validate(task, value)
                    if not result.allowed:
                        self.log_event(
                            "task.invalid_transition",
                            task_id=task_id,
                            message=result.detail,
                            from_status=result.from_status.value,
                            to_status=result.to_status.value,
                        )
                        # Block invalid transition — design principle:
                        # state integrity > backward compatibility
                        continue
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
            self._routing_table.register_agent(agent_id, agent_type)
            self.log_event("agent.registered", agent_id=agent_id, message=f"Registered {agent_type}")

    def set_agent_token(self, agent_id: str, token: Any) -> None:
        """Store a CapabilityToken for an agent.  Once set, send_structured
        verifies the token's HMAC signature before delivering messages from
        that agent.  Agents without a token are trusted (backward compat)."""
        from openagents_orchestration.enterprise.security import CapabilityToken
        if not isinstance(token, CapabilityToken):
            raise TypeError(f"Expected CapabilityToken, got {type(token).__name__}")
        if agent_id in self.agents:
            self.agents[agent_id]._capability_token = token

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from the board and routing table."""
        self.agents.pop(agent_id, None)
        self._routing_table.unregister_agent(agent_id)
        self.log_event("agent.unregistered", agent_id=agent_id)

    def update_agent(self, agent_id: str, **fields: Any) -> None:
        if agent_id not in self.agents:
            self.log_event(
                "agent.update_unknown",
                agent_id=agent_id,
                message=f"Attempted to update unknown agent: {agent_id}",
            )
            return
        agent = self.agents[agent_id]
        changed: list[str] = []
        for key, value in fields.items():
            if hasattr(agent, key):
                old = getattr(agent, key)
                # Coerce string status to AgentStatus enum
                if key == "status" and isinstance(value, str):
                    with contextlib.suppress(ValueError):
                        value = AgentStatus(value)
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
            existing = self.artifacts.get(path)
            if existing is not None:
                existing.claimed_by = task_id
                if existing.status != "verified":
                    existing.status = "claimed"
                continue
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
        # Infer trace_id from active traces when not explicitly provided
        trace_id: str | None = payload.get("trace_id")
        if trace_id is None:
            for key in (task_id, agent_id):
                if key and key in self._traces:
                    trace_id = self._traces[key].trace_id
                    break
        if trace_id:
            payload["trace_id"] = trace_id
        evt = Event(
            ts=time.time(),
            event_type=event_type,
            task_id=task_id,
            agent_id=agent_id,
            message=message,
            payload=dict(payload),
        )
        self.events.append(evt)
        # Truncate oldest events to prevent unbounded memory growth
        if len(self.events) > self._max_events:
            self.events = self.events[-self._max_events:]
        # Notify observers (fire-and-forget, errors must not propagate)
        for obs in self._observers:
            with contextlib.suppress(Exception):
                obs(evt)
        # Persist to JSONL if recorder is attached
        if self._recorder is not None:
            recorder_payload = dict(payload)
            recorder_payload.pop("trace_id", None)
            self._recorder.append(
                event_type,
                trace_id=trace_id,
                task_id=task_id,
                agent_id=agent_id,
                message=message,
                **recorder_payload,
            )
        if self._echo:
            ts_str = time.strftime("%H:%M:%S", time.localtime(evt.ts))
            parts = [f"[{ts_str}]"]
            if agent_id:
                parts.append(f"[{agent_id}]")
            parts.append(event_type)
            if task_id:
                parts.append(f"task={task_id}")
            if trace_id:
                parts.append(f"trace={trace_id}")
            if message:
                parts.append(f"— {message}")
            print(" ".join(parts), file=sys.stderr, flush=True)
        # Trigger snapshot if threshold reached
        self._maybe_snapshot()

    def _maybe_snapshot(self) -> None:
        """Trigger periodic snapshot if snapshotter is attached and interval elapsed.

        Throttled to ``_snapshot_interval_s`` seconds to prevent IO storms
        during high-frequency mutation bursts (e.g. 50 agents emitting
        events in the same second).
        """
        if self._snapshotter is None:
            return
        now = time.time()
        if now - self._last_snapshot_ts >= self._snapshot_interval_s:
            self._last_snapshot_ts = now
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

    def add_usage(self, usage: Any) -> None:
        total_tokens = getattr(usage, "total_tokens", 0) or 0
        if total_tokens:
            self.add_tokens(int(total_tokens))

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

        # 观察 6: DecisionHistory 模式识别
        history_patterns = self._detect_failure_patterns(task.agent_type)
        if history_patterns:
            parts.extend(history_patterns)

        if not parts:
            return ""

        return (
            f"\n[StateBoard 观察 - 任务 {task_id}]\n"
            + "\n".join(f"- {p}" for p in parts)
            + "\n注意：以上是基于状态的观察供你参考，最终决定权在你。"
        )

    def _detect_failure_patterns(self, agent_type: str) -> list[str]:
        """Analyze DecisionHistory for patterns suggesting a change of strategy.

        Returns human-readable warnings for the Director.
        """
        warnings: list[str] = []
        summary = self.decision_history.summary()

        # Pattern 1: Global failure rate too high
        total = summary.get("total_decisions", 0)
        if total >= 4:
            success_rate = summary.get("success_rate", 1.0)
            if success_rate < 0.4:
                warnings.append(
                    f"全局成功率仅 {success_rate:.0%}（{summary['completed']}/{total}），"
                    f"建议回顾任务分解质量或调整 agent 配置。"
                )

        # Pattern 2: Consecutive failures for this agent type
        recent = self.decision_history.recent(10)
        consecutive_same_type_fails = 0
        for d in reversed(recent):
            if d.get("type") == agent_type and d.get("outcome") == "failed":
                consecutive_same_type_fails += 1
            else:
                break
        if consecutive_same_type_fails >= 3:
            warnings.append(
                f"最近 {consecutive_same_type_fails} 次 {agent_type} 类型 agent 全部失败，"
                f"建议换用不同 agent 类型或直接 ask_human。"
            )

        # Pattern 3: Same task retried too many times with same approach
        type_stats = summary.get("by_type", {}).get("spawn_agent", {})
        type_total = type_stats.get("total", 0)
        type_failed = type_stats.get("failed", 0)
        if type_total >= 3 and type_failed >= type_total * 0.7:
            warnings.append(
                f"spawn_agent 失败率 {type_failed}/{type_total}（{type_failed/type_total:.0%}），"
                f"spawn_agent 策略在当前场景下可能不适用。"
            )

        return warnings

    def strategy_signals(self) -> list[dict[str, str]]:
        """High-level advisory signals about the orchestration strategy itself.

        These appear in the snapshot so the Director can see them without
        parsing raw stats.  Each signal has a ``level`` (critical | warning | info)
        and a ``message``.
        """
        signals: list[dict[str, str]] = []
        summary = self.decision_history.summary()
        total = summary.get("total_decisions", 0)

        # Signal 1: Strategy appears futile
        if total >= 6 and summary.get("success_rate", 1.0) == 0.0:
            signals.append({
                "level": "critical",
                "message": f"All {total} decisions failed. Current strategy is not working. "
                           "Strongly recommend ask_human or radical replan.",
            })
        elif total >= 4 and summary.get("success_rate", 1.0) < 0.25:
            signals.append({
                "level": "warning",
                "message": f"Success rate {summary['success_rate']:.0%} ({summary['completed']}/{total}). "
                           "Consider different agent types or smaller task scope.",
            })

        # Signal 2: Budget efficiency
        if total >= 2:
            per_type = self.agent_type_budget_summary().get("by_type", {})
            for atype, stats in per_type.items():
                if stats.get("token_used", 0) > 50_000 and stats.get("completed", 0) == 0:
                    signals.append({
                        "level": "warning",
                        "message": f"{atype} has consumed {stats['token_used']} tokens with 0 completions. "
                                   "Stop spawning {atype} agents.",
                    })

        # Signal 3: Stuck on same task
        if total >= 5:
            recent = self.decision_history.recent(5)
            task_ids = [d.get("task") for d in recent if d.get("task")]
            if len(set(task_ids)) == 1 and all(d.get("outcome") == "failed" for d in recent):
                signals.append({
                    "level": "critical",
                    "message": f"Last 5 decisions all targeted task '{task_ids[0]}' and all failed. "
                               "This task may be impossible. Consider skipping.",
                })

        # Signal 4: Agents burning budget with no output
        idle_agents = [a for a in self.agents.values() if a.status == AgentStatus.RUNNING]
        if idle_agents:
            long_running = [a for a in idle_agents if (time.time() - a.start_time) > 300]
            if long_running:
                signals.append({
                    "level": "warning",
                    "message": f"{len(long_running)} agent(s) running >5min with no completion. "
                               "Consider stopping and respawning with smaller scope.",
                })

        return signals

    def suggest_tools(self) -> list[str]:
        """根据当前全局状态推荐下一步最可能需要的工具。"""
        suggestions: list[str] = []

        # 高优先级：需要 human 回复
        unanswered = self._human_channel.get_pending_questions(project_id=self.project_id)
        if unanswered:
            suggestions.append("check_messages")
            return suggestions

        # 高优先级：deadline overdue tasks need immediate action
        now = time.time()
        overdue = [
            t for t in self.tasks.values()
            if getattr(t, "deadline_s", 0) > 0 and t.deadline_s < now
            and t.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}
        ]
        if overdue:
            suggestions.extend(["spawn_resident", "ask_human"])

        # 高优先级：pending messages
        pending_count = sum(
            self._run_sync(mbox.size())
            for mbox in self._mailboxes.values()
        )
        if pending_count:
            suggestions.append("check_messages")

        # 有 failed 任务 → 先看原因再决定
        failed = [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]
        if failed:
            suggestions.extend(["read_file", "replan", "spawn_resident"])

        # 有 ready 的 pending 任务 → 调度（按优先级排序）
        ready = self.tasks_ready()
        if ready:
            suggestions.append("spawn_agent")
            if len(ready) > 1:
                suggestions.append("spawn_agent(batch)")

        # 预算紧张
        if self.budget.exhausted or self.budget.time_remaining_s < 120:
            suggestions.extend(["ask_human", "finalize"])

        # 全部完成
        if self.all_terminal():
            suggestions.append("finalize")

        # 有 running 任务 → 等待，但可用 observer 工具
        running = [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
        if running and not suggestions:
            suggestions.append("show_state")

        return suggestions

    # -- mailbox API (v2 — legacy sync wrapper over async Mailbox) ------------



    async def _deliver_to_targets(
        self,
        msg: StructuredMessage,
        topology: TopologyType,
        targets: list[str],
    ) -> bool:
        """Deliver a message to resolved targets (async).

        Populates causality, chains pipeline messages, and logs ``mail.routed``.
        Called from ``send_structured`` directly and from ``_sync_enqueue``
        via ``_run_sync``.
        """
        self._populate_causality_if_needed(msg)

        success = True
        chain_causality = msg.header.causality
        parent_id = msg.msg_id
        for agent_id in targets:
            if topology == TopologyType.PIPELINE and agent_id != targets[0]:
                chain_causality = chain_causality + (parent_id,)
                payload = dict(msg.payload)
                payload["pipeline_stage"] = agent_id
                chain_msg = StructuredMessage(
                    header=MessageHeader(
                        sender=msg.header.sender, recipient=agent_id,
                        msg_type=msg.header.msg_type, priority=msg.header.priority,
                        parent_id=parent_id, trace_id=msg.header.trace_id,
                        causality=chain_causality,
                    ),
                    payload=payload, text=msg.text,
                )
                parent_id = chain_msg.msg_id
                mbox = self._get_or_create_mailbox(agent_id)
                ok = await mbox.enqueue(chain_msg)
            else:
                mbox = self._get_or_create_mailbox(agent_id)
                ok = await mbox.enqueue(msg)
            success = success and ok

        self.log_event(
            "mail.routed",
            agent_id=msg.header.sender,
            message=f"topology={topology.value} targets={targets}",
            topology=topology.value,
            targets=targets,
        )
        return success

    @staticmethod
    def _populate_causality_if_needed(msg: StructuredMessage) -> None:
        """Populate the causality chain when a message is a reply (has parent_id).

        Extracted to avoid duplication between ``_sync_enqueue`` and
        ``send_structured``.
        """
        if msg.header.parent_id and not msg.header.causality:
            msg.header = MessageHeader(
                msg_id=msg.header.msg_id,
                parent_id=msg.header.parent_id,
                trace_id=msg.header.trace_id,
                parent_span_id=msg.header.parent_span_id,
                causality=(msg.header.parent_id,),
                idempotency_key=msg.header.idempotency_key,
                sender=msg.header.sender,
                recipient=msg.header.recipient,
                msg_type=msg.header.msg_type,
                priority=msg.header.priority,
                created_at=msg.header.created_at,
                ttl_s=msg.header.ttl_s,
                delivery_count=msg.header.delivery_count,
            )

    def _sync_enqueue(
        self, msg: StructuredMessage, *, bypass_policy: bool = False
    ) -> bool:
        """Synchronously enqueue a message.

        When *bypass_policy* is True (legacy ``send_mail`` path), channel
        policy is skipped — health monitors, event replayers, and human
        replies need this unconditionally.

        New code should use ``send_structured()`` which always enforces policy.
        """
        self.propagate_trace(msg)

        if bypass_policy:
            # send_mail() legacy contract: deliver to any target,
            # regardless of channel policy rules.
            topology, targets = self._routing_table.route(msg)
            if not targets:
                targets = [msg.header.recipient]  # unregistered target: create mailbox on-the-fly
        else:
            try:
                self._channel_policy.assert_allowed(msg.header.sender, msg.header.recipient)
            except ChannelPolicyError as exc:
                self.log_event(
                    "mail.policy_blocked",
                    agent_id=msg.header.sender,
                    message=str(exc),
                    to_id=msg.header.recipient,
                )
                return False
            topology, targets = self._routing_table.route(msg)
            if not targets:
                return True

        return self._run_sync(self._deliver_to_targets(msg, topology, targets))

    @staticmethod
    def _run_sync(coro: Any) -> Any:
        """Run a coroutine synchronously, handling nested event loops.

        Uses a module-level thread-pool singleton to avoid the overhead of
        creating a new executor on every call. This should only be used for
        trivial, fast mailbox operations.
        """
        try:
            loop = asyncio.get_running_loop()
            future = _sync_pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
        except RuntimeError:
            return asyncio.run(coro)

    def send_mail(self, from_id: str, to_id: str, content: str) -> None:
        """Synchronous legacy API — delivers via Mailbox v2.

        Intentionally bypasses channel policy so health monitors,
        event replayers, human replies, and other system callers always
        deliver regardless of the policy table.

        New code should prefer send_structured() for policy-aware delivery.
        """
        msg = StructuredMessage.from_text(from_id, to_id, content)
        self._sync_enqueue(msg, bypass_policy=True)
        self.log_event(
            "mail.sent",
            agent_id=from_id,
            message=f"To {to_id}: {content[:100]}",
            to_id=to_id,
            content=content,
        )

    def messages_for(self, recipient: str) -> list[dict[str, Any]]:
        """Synchronous legacy API — returns plain dicts from Mailbox v2."""
        mbox = self._get_or_create_mailbox(recipient)
        if isinstance(mbox, InMemoryMailbox):
            structured = mbox._sync_peek(limit=1000)
        else:
            structured = self._run_sync(mbox.peek(limit=1000))

        return [
            {
                "from": msg.sender,
                "to": msg.recipient,
                "content": msg.text,
                "ts": msg.header.created_at.timestamp(),
            }
            for msg in structured
        ]

    def clear_mail(self, recipient: str | None = None) -> int:
        """Clear Mailbox v2 queues for one or all agents."""
        cleared = 0
        if recipient is None:
            targets = list(self._mailboxes.keys())
        else:
            targets = [recipient]

        for agent_id in targets:
            mbox = self._mailboxes.get(agent_id)
            if mbox is None:
                continue
            if isinstance(mbox, InMemoryMailbox):
                cleared += mbox._sync_clear()
            else:
                # Redis/other backends: peek + ack all visible messages
                messages = self._run_sync(mbox.peek(limit=10_000))
                for msg in messages:
                    self._run_sync(mbox.ack(msg.msg_id))
                    cleared += 1

        if cleared > 0:
            self.log_event(
                "mail.cleared",
                message=f"Cleared {cleared} message(s) for {recipient or 'all'}",
            )
        return cleared

    async def send_structured(self, msg: StructuredMessage) -> bool:
        """New async API — delivers via Mailbox with back-pressure awareness.

        Uses the routing table to support broadcast, pubsub, pipeline and p2p.
        Enforces ChannelPolicy before delivery.
        Returns False when any target mailbox is full.

        Pipeline delivery chains messages via ``parent_id``: each downstream
        stage receives a copy of the message whose ``parent_id`` points to
        the preceding stage's ``msg_id``, forming a causal chain.
        """
        # Propagate trace context before delivery
        self.propagate_trace(msg)

        # Verify sender's capability token if one is registered
        agent = self.agents.get(msg.header.sender)
        if agent is not None and getattr(agent, "_capability_token", None) is not None:
            if not agent._capability_token.verify():
                self.log_event(
                    "mail.auth_failed",
                    agent_id=msg.header.sender,
                    message=f"Token verification failed for {msg.header.sender}",
                )
                return False

        # Enforce channel policy
        try:
            self._channel_policy.assert_allowed(msg.header.sender, msg.header.recipient)
        except ChannelPolicyError as exc:
            self.log_event(
                "mail.policy_blocked",
                agent_id=msg.header.sender,
                message=str(exc),
                to_id=msg.header.recipient,
            )
            return False

        topology, targets = self._routing_table.route(msg)
        if not targets:
            return True  # No recipients is not a failure

        ok = await self._deliver_to_targets(msg, topology, targets)
        # Wake collaboration loop on every message send (event-driven)
        for cb in self._mail_sent_callbacks:
            with contextlib.suppress(Exception):
                cb()
        return ok

    async def peek_mailbox(
        self,
        recipient: str,
        limit: int = 5,
        *,
        msg_type: str | None = None,
        sender: str | None = None,
        priority_min: int | None = None,
    ) -> list[StructuredMessage]:
        """Peek into the recipient's mailbox without consuming.

        Optional filters narrow results by type, sender, or minimum priority.
        """
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.peek(
            limit=limit, msg_type=msg_type, sender=sender, priority_min=priority_min,
        )

    async def claim_message(
        self, recipient: str, msg_id: str
    ) -> StructuredMessage | None:
        """Pull a specific message from the mailbox by msg_id.

        Returns None if not found or already in-flight/acked.  Caller must ack.
        """
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.dequeue_specific(msg_id)

    async def claim_messages(
        self, recipient: str, batch_size: int = 10
    ) -> list[StructuredMessage]:
        """Pull messages from the mailbox (dequeue).  Caller must ack() them."""
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.dequeue(batch_size=batch_size)

    async def ack_message(self, recipient: str, msg_id: str) -> None:
        """Acknowledge a message as fully processed."""
        mbox = self._get_or_create_mailbox(recipient)
        await mbox.ack(msg_id)

    async def nack_message(
        self, recipient: str, msg_id: str, reason: str = ""
    ) -> None:
        """Negative-acknowledge — triggers re-delivery or DLQ."""
        mbox = self._get_or_create_mailbox(recipient)
        await mbox.nack(msg_id, reason)

    async def dlq_replay(self, recipient: str, msg_id: str) -> bool:
        """Re-enqueue a dead-lettered message for the given recipient.

        Returns True if the message was found in the DLQ and replayed.
        Intended for use by the Director after inspecting ``inspect_dlq()``.
        """
        mbox = self._get_or_create_mailbox(recipient)
        ok = await mbox.dlq_replay(msg_id)
        if ok:
            self.log_event(
                "mail.dlq_replayed",
                agent_id=recipient,
                message=f"Replayed DLQ message {msg_id}",
                msg_id=msg_id,
            )
        return ok

    def bind_agent_to_task(self, agent_id: str, task_id: str) -> None:
        """Bind a resident agent to a task for iterative work."""
        task = self.tasks.get(task_id)
        if task is not None:
            task.assigned_agent = agent_id
            self.log_event(
                "task.bound",
                task_id=task_id,
                agent_id=agent_id,
                message=f"Task {task_id} bound to {agent_id}",
            )

    def unbind_agent_from_task(self, task_id: str) -> None:
        """Unbind agent from a task (task completed or failed)."""
        task = self.tasks.get(task_id)
        if task is not None and task.assigned_agent:
            old = task.assigned_agent
            task.assigned_agent = ""
            self.log_event(
                "task.unbound",
                task_id=task_id,
                agent_id=old,
                message=f"Task {task_id} unbound from {old}",
            )

    # -- project context (shared knowledge) ----------------------------------

    def add_test_report(self, module: str, result: str, passed: int, failed: int) -> None:
        """Add a test report to shared project context."""
        self.project_context["test_reports"].append({
            "module": module,
            "result": result[:2000],
            "passed": passed,
            "failed": failed,
            "ts": time.time(),
        })
        # Cap to prevent unbounded growth
        if len(self.project_context["test_reports"]) > 200:
            self.project_context["test_reports"] = self.project_context["test_reports"][-200:]
        self.log_event(
            "project.test_report",
            message=f"{module}: {passed} passed, {failed} failed",
        )

    def add_error_log(self, source: str, error: str, traceback: str = "") -> None:
        """Add an error to shared project context."""
        self.project_context["error_logs"].append({
            "source": source,
            "error": error[:500],
            "traceback": traceback[:2000],
            "ts": time.time(),
        })
        if len(self.project_context["error_logs"]) > 500:
            self.project_context["error_logs"] = self.project_context["error_logs"][-500:]

    def add_code_change(self, file_path: str, description: str, agent_id: str = "") -> None:
        """Record a code change in project context."""
        self.project_context["code_changes"].append({
            "file": file_path,
            "description": description[:500],
            "agent_id": agent_id,
            "ts": time.time(),
        })
        if len(self.project_context["code_changes"]) > 500:
            self.project_context["code_changes"] = self.project_context["code_changes"][-500:]

    def get_project_context(self) -> dict[str, Any]:
        """Return the shared project context for all agents."""
        return {
            "latest_test_report": self.project_context["test_reports"][-1] if self.project_context["test_reports"] else None,
            "recent_errors": self.project_context["error_logs"][-5:],
            "recent_changes": self.project_context["code_changes"][-10:],
            "shared_notes": self.project_context["shared_notes"],
        }

    # -- human communication -------------------------------------------------

    _MAX_HUMAN_MESSAGES = 1000

    def human_post(self, human_id: str, content: str, *, target_team: str = "") -> None:
        """Human proactively posts a message to a project or team."""
        self._human_channel.post_message(
            from_human=human_id,
            content=content,
            project_id=self.project_id,
            team_id=target_team,
            target_agent="director" if not target_team else "",
        )
        # Route to target team or director
        if target_team:
            self.send_mail("human", target_team, content)
        else:
            self.send_mail("human", "director", content)
        self.log_event(
            "human.post",
            message=content[:100],
            human_id=human_id,
            target_team=target_team,
        )

    def get_human_conversation(self) -> list[dict[str, Any]]:
        """Return full human conversation log (asks + posts)."""
        return [
            {
                "type": item["type"],
                "from": item.get("from_agent") or item.get("from_human"),
                "content": item.get("question") or item.get("content"),
                "ts": item["created_at"].timestamp() if item.get("created_at") else 0,
            }
            for item in self._human_channel.get_activity(project_id=self.project_id)
        ]

    # -- human questions -----------------------------------------------------

    def ask_human(self, question: str, *, options: str = "", from_agent: str = "") -> str:
        """Record a question for human input. Returns a question ID."""
        qid = self._human_channel.ask(
            project_id=self.project_id,
            from_agent=from_agent,
            question=question,
            options=options,
            team_id=self.team_id,
        )
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
        ok = self._human_channel.answer(qid, answer)
        if ok:
            # Send reply to director via mailbox
            self.send_mail("human", "director", f"[回复 {qid}] {answer}")
            self.log_event(
                "human.replied",
                message=f"{qid}: {answer[:100]}",
                qid=qid,
                answer=answer,
            )
        return ok

    def get_human_questions(self, answered: bool | None = None) -> list[dict[str, Any]]:
        """Return human questions. answered=None returns all."""
        if answered is None or answered is False:
            pending = self._human_channel.get_pending_questions(project_id=self.project_id)
        else:
            pending = []
        if answered is None or answered is True:
            answered_qs = self._human_channel.get_answered_questions(project_id=self.project_id)
        else:
            answered_qs = []

        result: list[dict[str, Any]] = []
        for q in pending:
            result.append({
                "id": q.qid,
                "from": q.from_agent,
                "question": q.question,
                "options": q.options,
                "answer": q.answer,
            })
        for q in answered_qs:
            result.append({
                "id": q.qid,
                "from": q.from_agent,
                "question": q.question,
                "options": q.options,
                "answer": q.answer,
            })
        return result

    def agent_type_budget_summary(self) -> dict[str, Any]:
        """Aggregate resource usage and success rate by agent type.

        Gives the Director visibility into which agent types are burning budget
        and which are delivering value.
        """
        by_type: dict[str, dict[str, Any]] = {}
        for agent in self.agents.values():
            bucket = by_type.setdefault(agent.agent_type, {
                "count": 0, "token_used": 0, "steps_used": 0,
                "completed": 0, "failed": 0, "avg_steps": 0,
            })
            bucket["count"] += 1
            bucket["token_used"] += agent.token_used
            bucket["steps_used"] += agent.steps_used
            if agent.status == AgentStatus.DONE:
                bucket["completed"] += 1
            elif agent.status == AgentStatus.FAILED:
                bucket["failed"] += 1

        for bucket in by_type.values():
            if bucket["count"]:
                bucket["avg_steps"] = round(bucket["steps_used"] / bucket["count"], 1)

        total_agents = sum(b["count"] for b in by_type.values())
        return {
            "total_agents": total_agents,
            "by_type": by_type,
        }

    # -- queries for Director ------------------------------------------------

    def has_actionable(self) -> bool:
        """True if there are pending/running tasks or the Director should take another step."""
        if self.budget.exhausted:
            return False
        if self._final_summary:
            return False
        actionable = {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.REVIEW,
            TaskStatus.FIX_NEEDED,
        }
        return any(t.status in actionable for t in self.tasks.values())

    def all_terminal(self) -> bool:
        """True when every task is in a terminal state (not in progress or review)."""
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
        unanswered = self._human_channel.get_pending_questions(project_id=self.project_id)
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

        return {
            "objective": self.objective,
            "project_id": self.project_id,
            "team_id": self.team_id,
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
            "human_channel": self._human_channel.to_dict(),
            "decision_history": [
                d for d in self.decision_history.recent(self.decision_history._max)
            ],
            "project_context": dict(self.project_context),
            "final_summary": self._final_summary,
            "_max_events": self._max_events,
            "_snapshot_interval_s": self._snapshot_interval_s,
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
        mailbox_backend: str = "memory",
        redis_url: str | None = None,
    ) -> StateBoard:
        """Reconstruct a StateBoard from a full state dict."""
        from openagents_orchestration.models.task import TaskNode

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
            mailbox_backend=mailbox_backend,
            redis_url=redis_url,
            project_id=data.get("project_id", ""),
            team_id=data.get("team_id", ""),
            max_events=data.get("_max_events", 10_000),
            snapshot_interval_s=data.get("_snapshot_interval_s", 5.0),
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

        # Restore residents
        try:
            from openagents_orchestration.core.resident import ResidentState
            for rid, rdata in data.get("residents", {}).items():
                board.residents[rid] = ResidentState(
                    resident_id=rdata.get("resident_id", rid),
                    agent_type=rdata.get("agent_type", "coder"),
                    status=rdata.get("status", "idle"),
                    latest_output=rdata.get("latest_output", ""),
                    latest_task=rdata.get("latest_task", ""),
                    token_used=rdata.get("token_used", 0),
                    message_count=rdata.get("message_count", 0),
                    error_count=rdata.get("error_count", 0),
                    start_time=rdata.get("start_time", time.time()),
                    last_active=rdata.get("last_active", time.time()),
                )
        except (ImportError, TypeError, ValueError, AttributeError) as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to restore residents from snapshot (%s: %s), skipping",
                type(exc).__name__, exc,
            )

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

        # Restore human channel
        human_channel_data = data.get("human_channel")
        if human_channel_data:
            board._human_channel = HumanChannel.from_dict(human_channel_data)

        if "project_context" in data:
            restored_context = dict(data.get("project_context") or {})
            board.project_context.update(restored_context)
        board._final_summary = data.get("final_summary", "")

        # Restore decision history for resume continuity
        for d in data.get("decision_history", []):
            board.decision_history.record(DecisionRecord(
                decision_type=d.get("decision", ""),
                task_id=d.get("task", ""),
                agent_id=d.get("agent", ""),
                agent_type=d.get("type", ""),
                reasoning=d.get("why", ""),
                outcome=d.get("outcome", "unknown"),
                artifacts_produced=list(d.get("artifacts", [])),
                error=d.get("error", ""),
                token_spent=int(d.get("token_spent", 0)),
                steps_used=int(d.get("steps_used", 0)),
            ))

        return board

    # -- snapshot for LLM ----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Structured snapshot for the Director LLM to read.

        Designed to be compact but complete — the LLM needs enough context
        to decide: spawn / replan / intervene / skip / finalize.

        Includes an incremental ``state_diff`` showing what changed since the
        last snapshot call, so the Director doesn't need to compare full task
        lists across cycles.
        """
        # --- incremental state diff ---
        current_status: dict[str, str] = {
            t.task_id: t.status.value for t in self.tasks.values()
        }
        state_diff: list[dict[str, str]] = []
        all_ids = set(current_status) | set(self._previous_task_status)
        for tid in all_ids:
            prev = self._previous_task_status.get(tid)
            curr = current_status.get(tid)
            if prev != curr:
                state_diff.append({
                    "task": tid,
                    "from": prev or "(new)",
                    "to": curr or "(removed)",
                })
        self._previous_task_status = current_status

        # Task summary
        task_lines: list[dict[str, Any]] = []
        for t in self.tasks.values():
            entry: dict[str, Any] = {
                "id": t.task_id,
                "desc": t.description,
                "agent": t.agent_type,
                "status": t.status.value,
                "deps": t.dependencies,
                "artifacts": t.actual_artifacts or t.expected_artifacts,
                "error": t.error,
                "priority": getattr(t, "priority", 0),
                "max_iterations": getattr(t, "max_iterations", 5),
            }
            deadline = getattr(t, "deadline_s", 0)
            if deadline > 0:
                entry["deadline_s"] = deadline
                entry["deadline_remaining_s"] = round(deadline - time.time(), 1)
            task_lines.append(entry)

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
        # Sort ready tasks by priority (descending) for the Director
        ready_sorted = sorted(ready, key=lambda t: (-getattr(t, "priority", 0), t.task_id))
        blocked = self.tasks_blocked()
        running = [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
        unanswered = self._human_channel.get_pending_questions(project_id=self.project_id)
        recent_human_posts = self._human_channel.get_messages(project_id=self.project_id)[-5:]

        # Per-agent pending message counts (LLM-readable and backend-agnostic)
        pending_messages: dict[str, int] = {}
        total_pending = 0
        for agent_id, mbox in self._mailboxes.items():
            if isinstance(mbox, InMemoryMailbox):
                count = len(mbox._buffer)
            else:
                count = self._run_sync(mbox.size())
            pending_messages[agent_id] = count
            total_pending += count

        # Detect expired deadlines
        now = time.time()
        overdue_tasks = [
            t.task_id for t in self.tasks.values()
            if getattr(t, "deadline_s", 0) > 0 and t.deadline_s < now
            and t.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}
        ]

        return {
            "objective": self.objective,
            "budget": self.budget.to_dict(),
            "agent_type_budget": self.agent_type_budget_summary(),
            "progress": self.progress_summary(),
            "tasks": task_lines,
            "agents": agent_lines,
            "residents": resident_lines,
            "artifacts": artifact_lines,
            "signals": {
                "ready_to_run": [t.task_id for t in ready_sorted],
                "ready_prioritized": [
                    {"id": t.task_id, "priority": getattr(t, "priority", 0)}
                    for t in ready_sorted
                ],
                "blocked": [t.task_id for t in blocked],
                "running": [t.task_id for t in running],
                "deadline_overdue": overdue_tasks,
                "needs_human": self.needs_human(),
                "pending_messages": total_pending,
                "pending_messages_by_agent": pending_messages,
                "unanswered_human_questions": len(unanswered),
                "waiting_for_human": [
                    {"id": q.qid, "question": q.question[:100]}
                    for q in unanswered
                ],
                "human_activity": {
                    "recent_posts": [
                        {"from": m.from_human, "content": m.content[:100]}
                        for m in recent_human_posts
                    ],
                    "post_count": len(self._human_channel.get_messages(project_id=self.project_id)),
                },
                "all_done": self.all_terminal(),
            },
            "state_diff": state_diff,
            "recent_events": recent_events,
            "decision_feedback": self.decision_history.summary(),
            "recent_decisions": self.decision_history.recent(15),
            "strategy_signals": self.strategy_signals(),
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
