"""GitHub Actions CI operations via `gh` CLI."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.github._base import _format_result, _run_gh


class GitHubCITool(ToolPlugin):
    """CI/workflow operations: list runs, view status, get logs, rerun."""

    name = "github_ci"
    description = (
        "GitHub Actions CI operations. Use to check workflow status, "
        "view logs, or rerun failed jobs. Repo format: 'owner/repo'."
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
                    "enum": ["list", "status", "logs", "rerun", "cancel"],
                    "description": "CI action to perform.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in 'owner/repo' format.",
                },
                "run_id": {
                    "type": "string",
                    "description": "Workflow run ID (required for status/logs/rerun/cancel).",
                },
                "branch": {
                    "type": "string",
                    "description": "Filter by branch (for action=list).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max runs to list (for action=list).",
                    "default": 10,
                },
                "status_filter": {
                    "type": "string",
                    "enum": ["completed", "action_required", "cancelled", "failure", "in_progress", "neutral", "queued", "requested", "skipped", "stale", "success", "timed_out", "waiting"],
                    "description": "Filter runs by status (for action=list).",
                },
                "workflow": {
                    "type": "string",
                    "description": "Workflow file name, e.g. 'ci.yml' (for action=list).",
                },
                "job": {
                    "type": "string",
                    "description": "Job name to get logs for (for action=logs). If omitted, gets all job logs.",
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
            "list": self._do_list,
            "status": self._do_status,
            "logs": self._do_logs,
            "rerun": self._do_rerun,
            "cancel": self._do_cancel,
        }.get(action)

        if handler is None:
            raise ToolError(f"Unknown action: {action}", tool_name=self.name)

        return await handler(params, repo)

    async def _do_list(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        limit = int(params.get("limit", 10))
        branch = str(params.get("branch", "")).strip()
        status_filter = str(params.get("status_filter", "")).strip()
        workflow = str(params.get("workflow", "")).strip()

        cmd = ["run", "list", "--limit", str(limit), "--json",
               "databaseId,workflowName,displayTitle,status,conclusion,"
               "event,headBranch,headSha,createdAt,updatedAt,url"]
        if branch:
            cmd.extend(["--branch", branch])
        if status_filter:
            cmd.extend(["--status", status_filter])
        if workflow:
            cmd.extend(["--workflow", workflow])

        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(result, summary=f"CI runs in {repo}")

    async def _do_status(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        run_id = str(params.get("run_id", "")).strip()
        if not run_id:
            raise ToolError("status requires 'run_id'", tool_name=self.name)

        result = _run_gh(
            ["run", "view", run_id, "--json",
             "databaseId,workflowName,displayTitle,status,conclusion,"
             "event,headBranch,headSha,createdAt,updatedAt,url,jobs"],
            repo=repo,
            timeout=30,
        )
        return _format_result(result, summary=f"CI run {run_id} status in {repo}")

    async def _do_logs(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        run_id = str(params.get("run_id", "")).strip()
        if not run_id:
            raise ToolError("logs requires 'run_id'", tool_name=self.name)

        job = str(params.get("job", "")).strip()
        cmd = ["run", "view", run_id, "--log"]
        if job:
            cmd.extend(["--job", job])

        result = _run_gh(cmd, repo=repo, timeout=60)
        return _format_result(
            result,
            summary=f"CI logs for run {run_id} in {repo}",
            max_chars=15000,
        )

    async def _do_rerun(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        run_id = str(params.get("run_id", "")).strip()
        if not run_id:
            raise ToolError("rerun requires 'run_id'", tool_name=self.name)

        result = _run_gh(["run", "rerun", run_id], repo=repo, timeout=60)
        return _format_result(
            result,
            summary=f"Rerunning CI run {run_id} in {repo}",
        )

    async def _do_cancel(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        run_id = str(params.get("run_id", "")).strip()
        if not run_id:
            raise ToolError("cancel requires 'run_id'", tool_name=self.name)

        result = _run_gh(["run", "cancel", run_id], repo=repo, timeout=30)
        return _format_result(
            result,
            summary=f"Cancelled CI run {run_id} in {repo}",
        )
