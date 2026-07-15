"""OpenAgents Task Orchestration — multi-agent orchestration engine."""

__all__ = [
    "OrchestratorRunner",
    "StateBoard",
    "GlobalOrchestrator",
    "Project",
    "Team",
    "TeamSpec",
    "HumanChannel",
    "MonitorAgent",
    "ChannelPolicy",
    "CapabilityToken",
    "AgentIdentity",
    "AuditLog",
    "OrchestrationMetrics",
]


def __getattr__(name: str):
    if name == "OrchestratorRunner":
        from openagents_orchestration.core.runner import OrchestratorRunner
        return OrchestratorRunner
    if name == "StateBoard":
        from openagents_orchestration.core.state_board import StateBoard
        return StateBoard
    if name == "GlobalOrchestrator":
        from openagents_orchestration.projects.global_orchestrator import (
            GlobalOrchestrator,
        )
        return GlobalOrchestrator
    if name == "Project":
        from openagents_orchestration.projects.project import Project
        return Project
    if name == "Team":
        from openagents_orchestration.projects.team import Team
        return Team
    if name == "TeamSpec":
        from openagents_orchestration.projects.team import TeamSpec
        return TeamSpec
    if name == "HumanChannel":
        from openagents_orchestration.projects.human_channel import HumanChannel
        return HumanChannel
    if name == "MonitorAgent":
        from openagents_orchestration.projects.monitor_agent import MonitorAgent
        return MonitorAgent
    if name == "ChannelPolicy":
        from openagents_orchestration.transport.channel_policy import ChannelPolicy
        return ChannelPolicy
    if name == "CapabilityToken":
        from openagents_orchestration.projects.security import CapabilityToken
        return CapabilityToken
    if name == "AgentIdentity":
        from openagents_orchestration.projects.security import AgentIdentity
        return AgentIdentity
    if name == "AuditLog":
        from openagents_orchestration.projects.security import AuditLog
        return AuditLog
    if name == "OrchestrationMetrics":
        from openagents_orchestration.projects.metrics import OrchestrationMetrics
        return OrchestrationMetrics
    raise AttributeError(name)
