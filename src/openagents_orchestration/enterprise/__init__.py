"""Enterprise extensions — multi-project orchestration."""

from openagents_orchestration.enterprise.events import OrchestrationEvent
from openagents_orchestration.enterprise.human_channel import HumanChannel, HumanMessage, HumanQuestion
from openagents_orchestration.enterprise.metrics import OrchestrationMetrics
from openagents_orchestration.enterprise.monitor_agent import MonitorAgent
from openagents_orchestration.enterprise.security import AgentIdentity, AuditLog, CapabilityToken

# Lazy imports to avoid circular dependency with core.state_board.


def __getattr__(name: str):
    if name == "GlobalOrchestrator":
        from openagents_orchestration.enterprise.global_orchestrator import GlobalOrchestrator
        return GlobalOrchestrator
    if name == "Project":
        from openagents_orchestration.enterprise.project import Project
        return Project
    if name == "ProjectStatus":
        from openagents_orchestration.enterprise.project import ProjectStatus
        return ProjectStatus
    if name == "Team":
        from openagents_orchestration.enterprise.team import Team
        return Team
    if name == "TeamSpec":
        from openagents_orchestration.enterprise.team import TeamSpec
        return TeamSpec
    if name == "TeamStatus":
        from openagents_orchestration.enterprise.team import TeamStatus
        return TeamStatus
    raise AttributeError(name)


__all__ = [
    "OrchestrationEvent",
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
