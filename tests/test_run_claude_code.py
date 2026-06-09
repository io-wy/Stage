"""Tests for run_claude_code tool."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import patch

import pytest
from openagents.errors.exceptions import PermanentToolError, ToolError

from openagents_orchestration.tools.corecoder.run_claude_code import RunClaudeCodeTool


class TestRunClaudeCodeTool:
    def test_schema_and_spec(self):
        tool = RunClaudeCodeTool()
        spec = tool.execution_spec()
        assert spec.concurrency_safe is False
        assert spec.side_effects == "external"
        schema = tool.schema()
        props = schema.get("properties", {})
        assert "instruction" in props
        assert "files" in props
        assert "allowed_tools" in props
        assert "skip_permissions" in props
        assert "timeout" in props
        assert schema.get("required") == ["instruction"]

    def test_invoke_missing_instruction(self):
        tool = RunClaudeCodeTool()
        with pytest.raises(ToolError, match="instruction is required"):
            asyncio.run(tool.invoke({"files": ["x.py"]}, None))

    @patch("shutil.which")
    def test_invoke_claude_not_found(self, mock_which):
        mock_which.return_value = None
        tool = RunClaudeCodeTool()
        with pytest.raises(PermanentToolError, match="claude CLI not found"):
            asyncio.run(tool.invoke({"instruction": "hello"}, None))

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_basic(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["claude", "-p", "hello"],
            returncode=0,
            stdout="Hello, world!",
            stderr="",
        )

        tool = RunClaudeCodeTool()
        result = asyncio.run(tool.invoke({"instruction": "hello"}, None))

        assert result["exit_code"] == 0
        assert "Hello, world!" in result["output"]
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/usr/bin/claude"
        assert "-p" in args
        assert "hello" in args

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_with_files(self, mock_run, mock_which, tmp_path):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="done",
            stderr="",
        )

        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        tool = RunClaudeCodeTool()
        result = asyncio.run(
            tool.invoke(
                {"instruction": "review this", "files": [str(f)]},
                None,
            )
        )

        assert result["exit_code"] == 0
        args = mock_run.call_args[0][0]
        assert str(f) in args

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_with_allowed_tools(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ok",
            stderr="",
        )

        tool = RunClaudeCodeTool()
        asyncio.run(
            tool.invoke(
                {
                    "instruction": "do something",
                    "allowed_tools": ["Read", "Bash"],
                },
                None,
            )
        )

        args = mock_run.call_args[0][0]
        assert any("--allowedTools=Read,Bash" in str(a) for a in args)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_skip_permissions(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ok",
            stderr="",
        )

        tool = RunClaudeCodeTool()
        asyncio.run(
            tool.invoke(
                {"instruction": "do something", "skip_permissions": True},
                None,
            )
        )

        args = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in args

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_timeout(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude", "-p", "slow"],
            timeout=5,
            output=b"partial",
            stderr=b"",
        )

        tool = RunClaudeCodeTool()
        with pytest.raises(ToolError, match="timed out"):
            asyncio.run(
                tool.invoke(
                    {"instruction": "slow", "timeout": 5},
                    None,
                )
            )

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_nonzero_exit(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="some output",
            stderr="error details",
        )

        tool = RunClaudeCodeTool()
        result = asyncio.run(tool.invoke({"instruction": "fail"}, None))

        assert result["exit_code"] == 1
        assert "some output" in result["output"]
        assert "error details" in result["output"]

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_invoke_truncates_long_output(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="x" * 20_000,
            stderr="",
        )

        tool = RunClaudeCodeTool()
        result = asyncio.run(tool.invoke({"instruction": "big"}, None))

        assert len(result["output"]) < 10_000
        assert "truncated" in result["output"]
