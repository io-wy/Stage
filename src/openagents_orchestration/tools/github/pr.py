"""GitHub PR operations via `gh` CLI."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.github._base import _format_result, _run_gh


class GitHubPRTool(ToolPlugin):
    """Pull Request operations: create, view, list, review, merge, close, diff."""

    name = "github_pr"
    description = (
        "GitHub Pull Request operations. Use to create PRs, review them, "
        "merge, close, or view details. All operations require a repo "
        "in 'owner/repo' format (e.g. 'myorg/backend')."
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
                    "enum": ["create", "get", "list", "review", "merge", "close", "diff"],
                    "description": "PR action to perform.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in 'owner/repo' format.",
                },
                "number": {
                    "type": "integer",
                    "description": "PR number (required for get/review/merge/close/diff).",
                },
                "title": {
                    "type": "string",
                    "description": "PR title (required for create).",
                },
                "body": {
                    "type": "string",
                    "description": "PR body or review comment text.",
                },
                "base": {
                    "type": "string",
                    "description": "Base branch for PR creation (default: main).",
                    "default": "main",
                },
                "head": {
                    "type": "string",
                    "description": "Head branch for PR creation. Required for create.",
                },
                "review_action": {
                    "type": "string",
                    "enum": ["approve", "request-changes", "comment"],
                    "description": "Type of review (for action=review).",
                    "default": "comment",
                },
                "merge_strategy": {
                    "type": "string",
                    "enum": ["merge", "squash", "rebase"],
                    "description": "Merge strategy (for action=merge).",
                    "default": "merge",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max PRs to list (for action=list).",
                    "default": 10,
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "merged", "all"],
                    "description": "Filter PRs by state (for action=list).",
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
            "review": self._do_review,
            "merge": self._do_merge,
            "close": self._do_close,
            "diff": self._do_diff,
        }.get(action)

        if handler is None:
            raise ToolError(f"Unknown action: {action}", tool_name=self.name)

        return await handler(params, repo)

    async def _do_create(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        title = str(params.get("title", "")).strip()
        head = str(params.get("head", "")).strip()
        if not title or not head:
            raise ToolError(
                "create requires 'title' and 'head' (branch name)",
                tool_name=self.name,
            )
        body = str(params.get("body", "")).strip()
        base = str(params.get("base", "main")).strip() or "main"

        cmd = [
            "pr", "create",
            "--title", title,
            "--head", head,
            "--base", base,
        ]
        if body:
            cmd.extend(["--body", body])
        else:
            cmd.append("--fill")

        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(
            result,
            summary=f"Created PR from {head} to {base} in {repo}",
        )

    async def _do_get(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("get requires 'number' (PR number)", tool_name=self.name)

        result = _run_gh(
            [
                "pr", "view", str(number),
                "--json",
                "number,title,body,state,author,headRefName,baseRefName,"
                "mergeStateStatus,mergeable,reviewDecision,comments,"
                "changedFiles,additions,deletions,url,createdAt,updatedAt",
            ],
            repo=repo,
            timeout=30,
        )
        return _format_result(result, summary=f"PR #{number} in {repo}")

    async def _do_list(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        state = str(params.get("state", "open")).strip() or "open"
        limit = int(params.get("limit", 10))
        cmd = [
            "pr", "list",
            "--state", state,
            "--limit", str(limit),
            "--json",
            "number,title,author,headRefName,baseRefName,state,url,createdAt,updatedAt",
        ]
        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(
            result,
            summary=f"Listed {state} PRs in {repo}",
        )

    async def _do_review(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("review requires 'number' (PR number)", tool_name=self.name)

        review_action = str(params.get("review_action", "comment")).strip() or "comment"
        body = str(params.get("body", "")).strip()

        cmd = ["pr", "review", str(number), f"--{review_action}"]
        if body:
            cmd.extend(["--body", body])

        result = _run_gh(cmd, repo=repo, timeout=30)
        return _format_result(
            result,
            summary=f"Reviewed PR #{number} ({review_action}) in {repo}",
        )

    async def _do_merge(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("merge requires 'number' (PR number)", tool_name=self.name)

        strategy = str(params.get("merge_strategy", "merge")).strip() or "merge"
        cmd = ["pr", "merge", str(number), f"--{strategy}", "--auto"]
        result = _run_gh(cmd, repo=repo, timeout=60)
        return _format_result(
            result,
            summary=f"Merged PR #{number} ({strategy}) in {repo}",
        )

    async def _do_close(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("close requires 'number' (PR number)", tool_name=self.name)

        result = _run_gh(["pr", "close", str(number)], repo=repo, timeout=30)
        return _format_result(result, summary=f"Closed PR #{number} in {repo}")

    async def _do_diff(self, params: dict[str, Any], repo: str) -> dict[str, Any]:
        number = params.get("number")
        if number is None:
            raise ToolError("diff requires 'number' (PR number)", tool_name=self.name)

        result = _run_gh(["pr", "diff", str(number)], repo=repo, timeout=30)
        out = result.get("stdout", "")
        if result["success"] and out:
            # Count files from diff headers
            files = [line for line in out.splitlines() if line.startswith("diff --git")]
            summary = f"PR #{number} diff: {len(files)} file(s) changed"
        else:
            summary = f"PR #{number} diff in {repo}"
        return _format_result(result, summary=summary, max_chars=12000)
