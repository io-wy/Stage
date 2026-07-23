"""Stage service governance platform."""

__all__ = [
    "OrchestratorRunner",
    "StateBoard",
    "GlobalOrchestrator",
    "Project",
    "Team",
    "TeamSpec",
    "HumanChannel",
    "ChannelPolicy",
    "CapabilityToken",
    "AgentIdentity",
    "AuditLog",
]


def __getattr__(name: str):
    if name == "OrchestratorRunner":
        from openagents_orchestration.runtime.runner import OrchestratorRunner
        return OrchestratorRunner
    if name == "StateBoard":
        from openagents_orchestration.runtime.state_board import StateBoard
        return StateBoard
    if name == "GlobalOrchestrator":
        from openagents_orchestration.runtime.global_orchestrator import (
            GlobalOrchestrator,
        )
        return GlobalOrchestrator
    if name == "Project":
        from openagents_orchestration.runtime.project import Project
        return Project
    if name == "Team":
        from openagents_orchestration.runtime.team import Team
        return Team
    if name == "TeamSpec":
        from openagents_orchestration.runtime.team import TeamSpec
        return TeamSpec
    if name == "HumanChannel":
        from openagents_orchestration.runtime.human_channel import HumanChannel
        return HumanChannel
    if name == "ChannelPolicy":
        from openagents_orchestration.transport.channel_policy import ChannelPolicy
        return ChannelPolicy
    if name == "CapabilityToken":
        from openagents_orchestration.runtime.security import CapabilityToken
        return CapabilityToken
    if name == "AgentIdentity":
        from openagents_orchestration.runtime.security import AgentIdentity
        return AgentIdentity
    if name == "AuditLog":
        from openagents_orchestration.runtime.security import AuditLog
        return AuditLog
    raise AttributeError(name)
