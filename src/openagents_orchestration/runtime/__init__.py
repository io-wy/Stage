"""Legacy runtime compatibility layer."""

from openagents_orchestration.runtime.human_channel import (
    HumanChannel,
    HumanMessage,
    HumanQuestion,
)
from openagents_orchestration.runtime.security import (
    AgentIdentity,
    AuditLog,
    CapabilityToken,
)

# Lazy imports avoid circular dependencies among runtime modules.


def __getattr__(name: str):
    if name == "GlobalOrchestrator":
        from openagents_orchestration.runtime.global_orchestrator import (
            GlobalOrchestrator,
        )
        return GlobalOrchestrator
    if name == "Project":
        from openagents_orchestration.runtime.project import Project
        return Project
    if name == "ProjectStatus":
        from openagents_orchestration.runtime.project import ProjectStatus
        return ProjectStatus
    if name == "Team":
        from openagents_orchestration.runtime.team import Team
        return Team
    if name == "TeamSpec":
        from openagents_orchestration.runtime.team import TeamSpec
        return TeamSpec
    if name == "TeamStatus":
        from openagents_orchestration.runtime.team import TeamStatus
        return TeamStatus
    raise AttributeError(name)


__all__ = [
    "GlobalOrchestrator",
    "HumanChannel",
    "HumanMessage",
    "HumanQuestion",
    "Project",
    "ProjectStatus",
    "AgentIdentity",
    "AuditLog",
    "CapabilityToken",
    "Team",
    "TeamSpec",
    "TeamStatus",
]
