"""GitHub repository operations via `gh` CLI."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.github._base import _format_result, _run_gh


class GitHubRepoTool(ToolPlugin):
    """Repository operations: clone, view repo info, list branches, create branch."""

    name = "github_repo"
    description = (
        "GitHub repository operations. Use to clone repos, view repo details, "
        "list branches, or check repository status. Repo format: 'owner/repo'."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=120_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["clone", "info", "branches", "fork", "view_file"],
                    "description": "Repository action to perform.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in 'owner/repo' format.",
                },
                "directory": {
                    "type": "string",
                    "description": "Local directory for clone (default: repo name).",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name (for view_file).",
                    "default": "main",
                },
                "path": {
                    "type": "string",
                    "description": "File path within repo (for view_file).",
                },
                "depth": {
                    "type": "integer",
                    "description": "Clone depth, 0 for full history (default: 1).",
                    "default": 1,
                },
            },
            "required": ["action", "repo"],
        }

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        action = str(params.get("action", "")).strip()
        repo = str(params.get("repo", "")).strip()

        if not action:
            raise ToolError("action is required", tool_name=self.name)
        if not repo:
            raise ToolError("repo is required (format: owner/repo)", tool_name=self.name)

        handler = {
            "clone": self._do_clone,
            "info": self._do_info,
            "branches": self._do_branches,
            "fork": self._do_fork,
            "view_file": self._do_view_file,
        }.get(action)

        if handler is None:
            raise ToolError(f"Unknown action: {action}", tool_name=self.name)

        return await handler(params, repo)

    async def _do_clone(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        directory = str(params.get("directory", "")).strip()
        depth = int(params.get("depth", 1))

        cmd = ["repo", "clone", repo]
        if directory:
            cmd.append(directory)
        if depth == 1:
            cmd.append("--")
            cmd.append("--depth=1")
        elif depth > 1:
            cmd.append("--")
            cmd.append(f"--depth={depth}")

        result = _run_gh(cmd, timeout=120)
        target_dir = directory or repo.split("/")[-1]
        return _format_result(
            result,
            summary=f"Cloned {repo} into ./{target_dir}",
        )

    async def _do_info(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        result = _run_gh(
            ["repo", "view", repo, "--json",
             "name,owner,description,defaultBranch,primaryLanguage,"
             "stargazerCount,forkCount,openIssueCount,openPullRequestCount,"
             "pushedAt,createdAt,url"],
            timeout=30,
        )
        return _format_result(result, summary=f"Repository info for {repo}")

    async def _do_branches(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        result = _run_gh(
            ["api", f"repos/{repo}/branches?per_page=30"],
            timeout=30,
        )
        return _format_result(result, summary=f"Branches in {repo}")

    async def _do_fork(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        result = _run_gh(["repo", "fork", repo], timeout=60)
        return _format_result(result, summary=f"Forked {repo}")

    async def _do_view_file(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        path = str(params.get("path", "")).strip()
        if not path:
            raise ToolError("view_file requires 'path'", tool_name=self.name)

        branch = str(params.get("branch", "main")).strip() or "main"
        result = _run_gh(
            ["api", f"repos/{repo}/contents/{path}?ref={branch}"],
            timeout=30,
        )
        return _format_result(
            result,
            summary=f"File {path}@{branch} in {repo}",
            max_chars=12000,
        )
