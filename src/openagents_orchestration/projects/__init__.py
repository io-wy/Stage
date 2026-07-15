"""Multi-project orchestration extensions."""

from openagents_orchestration.projects.human_channel import (
    HumanChannel,
    HumanMessage,
    HumanQuestion,
)
from openagents_orchestration.projects.metrics import OrchestrationMetrics
from openagents_orchestration.projects.monitor_agent import MonitorAgent
from openagents_orchestration.projects.security import (
    AgentIdentity,
    AuditLog,
    CapabilityToken,
)

# Lazy imports to avoid circular dependency with core.state_board.


def __getattr__(name: str):
    if name == "GlobalOrchestrator":
        from openagents_orchestration.projects.global_orchestrator import (
            GlobalOrchestrator,
        )
        return GlobalOrchestrator
    if name == "Project":
        from openagents_orchestration.projects.project import Project
        return Project
    if name == "ProjectStatus":
        from openagents_orchestration.projects.project import ProjectStatus
        return ProjectStatus
    if name == "Team":
        from openagents_orchestration.projects.team import Team
        return Team
    if name == "TeamSpec":
        from openagents_orchestration.projects.team import TeamSpec
        return TeamSpec
    if name == "TeamStatus":
        from openagents_orchestration.projects.team import TeamStatus
        return TeamStatus
    raise AttributeError(name)


__all__ = [
    "GlobalOrchestrator",
    "HumanChannel",
    "HumanMessage",
    "HumanQuestion",
    "OrchestrationMetrics",
    "MonitorAgent",
    "Project",
    "ProjectStatus",
    "AgentIdentity",
    "AuditLog",
    "CapabilityToken",
    "Team",
    "TeamSpec",
    "TeamStatus",
]
