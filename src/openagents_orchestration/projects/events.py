"""OrchestrationEvent — enterprise event bus enumeration.

All mutation events across Project, Team, Agent, Task, and Human
are typed via OrchestrationEvent for structured logging, metrics,
and cross-project routing.
"""

from __future__ import annotations

from enum import StrEnum


class OrchestrationEvent(StrEnum):
    """Canonical event types emitted by the orchestration engine."""

    # Project lifecycle
    PROJECT_CREATED = "project.created"
    PROJECT_STARTED = "project.started"
    PROJECT_PAUSED = "project.paused"
    PROJECT_RESUMED = "project.resumed"
    PROJECT_COMPLETED = "project.completed"
    PROJECT_FAILED = "project.failed"
    PROJECT_TERMINATED = "project.terminated"

    # Team lifecycle
    TEAM_CREATED = "team.created"
    TEAM_STARTED = "team.started"
    TEAM_PAUSED = "team.paused"
    TEAM_RESUMED = "team.resumed"
    TEAM_COMPLETED = "team.completed"
    TEAM_FAILED = "team.failed"
    TEAM_STOPPED = "team.stopped"

    # Agent lifecycle
    AGENT_SPAWNED = "agent.spawned"
    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"
    AGENT_SLEEPING = "agent.sleeping"
    AGENT_WOKEN = "agent.woken"
    AGENT_UPDATING = "agent.updating"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_HEARTBEAT_TIMEOUT = "agent.heartbeat_timeout"
    AGENT_ERROR = "agent.error"

    # Task lifecycle
    TASK_ADDED = "task.added"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_ASSIGNED = "task.assigned"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_RETRY = "task.retry"

    # Human interaction
    HUMAN_QUESTION_ASKED = "human.question_asked"
    HUMAN_QUESTION_ANSWERED = "human.question_answered"
    HUMAN_MESSAGE_POSTED = "human.message_posted"

    # Messaging
    MESSAGE_SENT = "message.sent"
    MESSAGE_DELIVERED = "message.delivered"
    MESSAGE_DLQ = "message.dlq"
    CROSS_PROJECT_MESSAGE = "cross_project.message"
    DLQ_MESSAGE_DETECTED = "dlq.message_detected"

    # Budget / Resource
    BUDGET_TOKENS = "budget.tokens"
    BUDGET_STEPS = "budget.steps"
    BUDGET_EXHAUSTED = "budget.exhausted"

    # System
    ORCHESTRATOR_STARTED = "orchestrator.started"
    ORCHESTRATOR_STOPPED = "orchestrator.stopped"
    ORCHESTRATOR_ERROR = "orchestrator.error"
    TRACE_STARTED = "trace.started"
    TRACE_ENDED = "trace.ended"

    @property
    def category(self) -> str:
        """Return the category prefix (e.g. 'project' for project.created)."""
        return self.value.split(".")[0]

    @property
    def is_project_level(self) -> bool:
        """True if this event should be visible at the Project level."""
        return self.category in {
            "project", "team", "agent", "task", "human",
            "message", "budget", "orchestrator", "trace",
        }

    @property
    def is_global_level(self) -> bool:
        """True if this event should bubble up to GlobalOrchestrator."""
        return self in {
            OrchestrationEvent.PROJECT_FAILED,
            OrchestrationEvent.PROJECT_COMPLETED,
            OrchestrationEvent.PROJECT_TERMINATED,
            OrchestrationEvent.TEAM_FAILED,
            OrchestrationEvent.TEAM_COMPLETED,
            OrchestrationEvent.AGENT_HEARTBEAT_TIMEOUT,
            OrchestrationEvent.BUDGET_EXHAUSTED,
            OrchestrationEvent.ORCHESTRATOR_ERROR,
            OrchestrationEvent.CROSS_PROJECT_MESSAGE,
            OrchestrationEvent.DLQ_MESSAGE_DETECTED,
        }
