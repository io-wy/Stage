"""GitHub Issue operations via `gh` CLI."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.github._base import _format_result, _run_gh


class GitHubIssueTool(ToolPlugin):
    """Issue operations: create, view, list, comment, close, label."""

    name = "github_issue"
    description = (
        "GitHub Issue operations. Use to create issues, add comments, "
        "close, label, or list issues. Repo format: 'owner/repo'."
    )
    durable_idempotent = False

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,
            side_effects="external",
            default_timeout_ms=60_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "get", "list", "comment", "close", "label"],
                    "description": "Issue action to perform.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in 'owner/repo' format.",
                },
                "number": {
                    "type": "integer",
                    "description": "Issue number (required for get/comment/close/label).",
                },
                "title": {
                    "type": "string",
                    "description": "Issue title (required for create).",
                },
                "body": {
                    "type": "string",
                    "description": "Issue body or comment text.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels to add (for action=label or create).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max issues to list (for action=list).",
                    "default": 10,
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": "Filter issues by state (for action=list).",
                    "default": "open",
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
            "create": self._do_create,
            "get": self._do_get,
            "list": self._do_list,
            "comment": self._do_comment,
            "close": self._do_close,
            "label": self._do_label,
        }.get(action)

        if handler is None:
            raise ToolError(f"Unknown action: {action}", tool_name=self.name)

        return await handler(params, repo)

    async def _do_create(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        title = str(params.get("title", "")).strip()
        if not title:
            raise ToolError("create requires 'title'", tool_name=self.name)

        body = str(params.get("body", "")).strip()
        labels = params.get("labels", [])

        cmd = ["issue", "create", "--title", title]
        if body:
            cmd.extend(["--body", body])
        for label in labels:
            cmd.extend(["--label", str(label)])

        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(result, summary=f"Created issue in {repo}")

    async def _do_get(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("get requires 'number'", tool_name=self.name)

        result = _run_gh(
            [
                "issue", "view", str(number),
                "--json",
                "number,title,body,state,author,labels,comments,"
                "url,createdAt,updatedAt,closedAt",
            ],
            repo=repo,
            timeout=30,
        )
        return _format_result(result, summary=f"Issue #{number} in {repo}")

    async def _do_list(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        state = str(params.get("state", "open")).strip() or "open"
        limit = int(params.get("limit", 10))
        cmd = [
            "issue", "list",
            "--state", state,
            "--limit", str(limit),
            "--json",
            "number,title,author,labels,state,url,createdAt,updatedAt",
        ]
        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(
            result,
            summary=f"Listed {state} issues in {repo}",
        )

    async def _do_comment(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("comment requires 'number'", tool_name=self.name)

        body = str(params.get("body", "")).strip()
        if not body:
            raise ToolError("comment requires 'body'", tool_name=self.name)

        result = _run_gh(
            ["issue", "comment", str(number), "--body", body],
            repo=repo,
            timeout=30,
        )
        return _format_result(
            result,
            summary=f"Commented on issue #{number} in {repo}",
        )

    async def _do_close(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("close requires 'number'", tool_name=self.name)

        result = _run_gh(["issue", "close", str(number)], repo=repo, timeout=30)
        return _format_result(result, summary=f"Closed issue #{number} in {repo}")

    async def _do_label(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("label requires 'number'", tool_name=self.name)

        labels = params.get("labels", [])
        if not labels:
            raise ToolError("label requires 'labels' list", tool_name=self.name)

        cmd = ["issue", "edit", str(number)]
        for label in labels:
            cmd.extend(["--add-label", str(label)])

        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(
            result,
            summary=f"Added labels to issue #{number} in {repo}",
        )
