"""Core engine — single-project orchestration."""

from openagents_orchestration.core.collaboration import CollaborationSignal, parse_collaboration_message
from openagents_orchestration.core.collaboration_executor import (
    CollaborationDecisionExecutor,
)
from openagents_orchestration.core.collaboration_state_machine import (
    CollaborationAction,
    CollaborationDecision,
    CollaborationStateMachine,
)
from openagents_orchestration.core.decision_history import DecisionHistory, DecisionRecord
from openagents_orchestration.core.resident import AgentLifecycle, ResidentAgent, ResidentState
from openagents_orchestration.core.state_board import AgentState, AgentStatus, ArtifactRecord, Budget, Event, StateBoard
from openagents_orchestration.core.sub_state_board import SubStateBoard
from openagents_orchestration.core.task_state_machine import TaskStateMachine

# Lazy imports to avoid circular dependency with runner <-> enterprise


def __getattr__(name: str):
    if name == "OrchestratorRunner":
        from openagents_orchestration.core.runner import OrchestratorRunner
        return OrchestratorRunner
    if name == "RunnerDeps":
        from openagents_orchestration.core.runner import RunnerDeps
        return RunnerDeps
    raise AttributeError(name)


__all__ = [
    "Budget",
    "CollaborationAction",
    "CollaborationDecision",
    "CollaborationDecisionExecutor",
    "CollaborationSignal",
    "CollaborationStateMachine",
    "DecisionHistory",
    "DecisionRecord",
    "parse_collaboration_message",
    "AgentLifecycle",
    "ResidentAgent",
    "ResidentState",
    "OrchestratorRunner",
    "RunnerDeps",
    "AgentState",
    "AgentStatus",
    "ArtifactRecord",
    "Event",
    "StateBoard",
    "SubStateBoard",
    "TaskStateMachine",
]
