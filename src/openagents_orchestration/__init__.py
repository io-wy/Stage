"""OpenAgents Task Orchestration — multi-agent orchestration engine."""

__all__ = ["OrchestratorRunner", "StateBoard"]


def __getattr__(name: str):
    if name == "OrchestratorRunner":
        from openagents_orchestration.runner import OrchestratorRunner

        return OrchestratorRunner
    if name == "StateBoard":
        from openagents_orchestration.state_board import StateBoard

        return StateBoard
    raise AttributeError(name)
