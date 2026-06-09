"""Tests for GitHub tools."""

from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import patch

import pytest
from openagents.errors.exceptions import ToolError

from openagents_orchestration.tools.github.ci import GitHubCITool
from openagents_orchestration.tools.github.issue import GitHubIssueTool
from openagents_orchestration.tools.github.pr import GitHubPRTool
from openagents_orchestration.tools.github.repo import GitHubRepoTool


def _make_auth_ok():
    """Return a mock for subprocess.run that passes auth checks."""
    auth_ok = subprocess.CompletedProcess(
        args=["gh", "auth", "status"],
        returncode=0,
        stdout="",
        stderr="",
    )

    def _run(cmd, **kwargs):
        # Auth check call
        if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
            return auth_ok
        # Should be overridden by specific test patches
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="unexpected call",
        )

    return _run


class TestGitHubPRTool:
    def test_schema_and_spec(self):
        tool = GitHubPRTool()
        spec = tool.execution_spec()
        assert spec.concurrency_safe is False
        schema = tool.schema()
        actions = schema["properties"]["action"]["enum"]
        assert set(actions) == {"create", "get", "list", "review", "merge", "close", "diff"}

    def test_invoke_missing_repo(self):
        tool = GitHubPRTool()
        with pytest.raises(ToolError):
            asyncio.run(tool.invoke({"action": "get"}, None))

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_get_pr(self, _which_mock):
        mock_data = {
            "number": 42,
            "title": "Fix auth",
            "state": "OPEN",
        }

        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(mock_data), stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubPRTool()
            result = asyncio.run(tool.invoke(
                {"action": "get", "repo": "myorg/backend", "number": 42},
                None,
            ))
        assert result["success"] is True
        assert result["data"]["number"] == 42

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_list_prs(self, _which_mock):
        mock_data = [
            {"number": 1, "title": "Feature A"},
            {"number": 2, "title": "Fix B"},
        ]

        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(mock_data), stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubPRTool()
            result = asyncio.run(tool.invoke(
                {"action": "list", "repo": "myorg/backend", "limit": 5},
                None,
            ))
        assert result["success"] is True
        assert len(result["data"]) == 2

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_diff_pr(self, _which_mock):
        diff_text = "diff --git a/main.py b/main.py\n+def hello():\n"

        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=diff_text, stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubPRTool()
            result = asyncio.run(tool.invoke(
                {"action": "diff", "repo": "myorg/backend", "number": 42},
                None,
            ))
        assert result["success"] is True
        assert "diff --git" in result["output"]

    def test_gh_not_installed(self):
        """When gh is not found, return structured error."""
        with patch("shutil.which", return_value=None):
            tool = GitHubPRTool()
            result = asyncio.run(tool.invoke(
                {"action": "list", "repo": "myorg/backend"},
                None,
            ))
        assert result["success"] is False
        assert "not installed" in result["error"]


class TestGitHubIssueTool:
    def test_schema(self):
        tool = GitHubIssueTool()
        schema = tool.schema()
        actions = schema["properties"]["action"]["enum"]
        assert "create" in actions
        assert "label" in actions

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_create_issue(self, _which_mock):
        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="https://github.com/myorg/backend/issues/7", stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubIssueTool()
            result = asyncio.run(tool.invoke(
                {
                    "action": "create",
                    "repo": "myorg/backend",
                    "title": "Bug in auth",
                    "body": "Steps to reproduce...",
                    "labels": ["bug", "high"],
                },
                None,
            ))
        assert result["success"] is True

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_get_issue(self, _which_mock):
        mock_data = {"number": 7, "title": "Bug", "state": "OPEN"}

        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(mock_data), stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubIssueTool()
            result = asyncio.run(tool.invoke(
                {"action": "get", "repo": "myorg/backend", "number": 7},
                None,
            ))
        assert result["success"] is True
        assert result["data"]["title"] == "Bug"


class TestGitHubCITool:
    def test_schema(self):
        tool = GitHubCITool()
        schema = tool.schema()
        actions = schema["properties"]["action"]["enum"]
        assert "list" in actions
        assert "logs" in actions

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_list_runs(self, _which_mock):
        mock_data = [
            {"databaseId": 123, "status": "completed", "conclusion": "success"},
        ]

        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(mock_data), stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubCITool()
            result = asyncio.run(tool.invoke(
                {"action": "list", "repo": "myorg/backend", "limit": 5},
                None,
            ))
        assert result["success"] is True
        assert len(result["data"]) == 1

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_rerun(self, _which_mock):
        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="Requested rerun of run 123", stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubCITool()
            result = asyncio.run(tool.invoke(
                {"action": "rerun", "repo": "myorg/backend", "run_id": "123"},
                None,
            ))
        assert result["success"] is True


class TestGitHubRepoTool:
    def test_schema(self):
        tool = GitHubRepoTool()
        schema = tool.schema()
        actions = schema["properties"]["action"]["enum"]
        assert "clone" in actions
        assert "info" in actions
        assert "branches" in actions

    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_repo_info(self, _which_mock):
        mock_data = {
            "name": "backend",
            "stargazerCount": 100,
            "openIssueCount": 5,
        }

        def _run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[1] == "auth" and cmd[2] == "status":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(mock_data), stderr="",
            )

        with patch("subprocess.run", side_effect=_run):
            tool = GitHubRepoTool()
            result = asyncio.run(tool.invoke(
                {"action": "info", "repo": "myorg/backend"},
                None,
            ))
        assert result["success"] is True
        assert result["data"]["name"] == "backend"
