"""GitHub tools for the GitHub Agent.

All tools use the `gh` CLI. The agent must have `gh` installed and authenticated
(`gh auth status` shows logged in state).
"""

from openagents_orchestration.tools.github.ci import GitHubCITool
from openagents_orchestration.tools.github.issue import GitHubIssueTool
from openagents_orchestration.tools.github.pr import GitHubPRTool
from openagents_orchestration.tools.github.repo import GitHubRepoTool

__all__ = [
    "GitHubPRTool",
    "GitHubIssueTool",
    "GitHubCITool",
    "GitHubRepoTool",
]
