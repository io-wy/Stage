"""Tests for RunClaudeCodeTool."""

from __future__ import annotations

import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from openagents.errors.exceptions import PermanentToolError, ToolError

from openagents_orchestration.tools.corecoder.run_claude_code import (
    _DEFAULT_ALLOWED_TOOLS,
    _DEFAULT_TIMEOUT_S,
    RunClaudeCodeTool,
)


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestRunClaudeCodeTool:
    def test_schema_has_required_fields(self):
        tool = RunClaudeCodeTool()
        schema = tool.schema()
        assert "instruction" in schema["properties"]
        assert "files" in schema["properties"]
        assert "allowed_tools" in schema["properties"]
        assert "skip_permissions" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert schema.get("required") == ["instruction"]

    @pytest.mark.asyncio
    async def test_invoke_claude_not_in_path(self):
        """When claude is not in PATH, should raise PermanentToolError."""
        with patch.object(shutil, "which", return_value=None):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            with pytest.raises(PermanentToolError, match="not found"):
                await tool.invoke({"instruction": "hello"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_success_exit_code_0(self):
        """Mock subprocess returning exit_code=0, verify result structure."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Hello from Claude Code\n"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({"instruction": "say hello"}, ctx)

            assert result["exit_code"] == 0
            assert "Hello from Claude Code" in result["output"]
            assert "claude -p" in result["command"]
            assert "say hello" in result["command"]

            # Verify subprocess.run was called with correct args
            call_args = mock_run.call_args
            assert call_args[1]["capture_output"] is True
            assert call_args[1]["text"] is True
            assert call_args[1]["timeout"] == _DEFAULT_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_invoke_exit_code_1(self):
        """Mock subprocess returning exit_code=1, verify error is in result."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "Some output\n"
        mock_result.stderr = "Error: something went wrong\n"

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({"instruction": "do something"}, ctx)

            assert result["exit_code"] == 1
            assert "Some output" in result["output"]
            assert "Error: something went wrong" in result["output"]
            assert "[stderr]" in result["output"]

    @pytest.mark.asyncio
    async def test_invoke_timeout_parameter(self):
        """Verify timeout parameter is passed through to subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({"instruction": "test", "timeout": 60}, ctx)

            assert result["exit_code"] == 0
            call_args = mock_run.call_args
            assert call_args[1]["timeout"] == 60

    @pytest.mark.asyncio
    async def test_invoke_allowed_tools_parameter(self):
        """Verify allowed_tools are passed as --allowedTools CLI arg."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            custom_tools = ["Read", "Bash"]
            result = await tool.invoke({
                "instruction": "test",
                "allowed_tools": custom_tools,
            }, ctx)

            assert result["exit_code"] == 0
            cmd = mock_run.call_args[0][0]
            assert any("--allowedTools=Read,Bash" in str(arg) for arg in cmd)

    @pytest.mark.asyncio
    async def test_invoke_default_allowed_tools(self):
        """Verify default allowed_tools are used when not specified."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({"instruction": "test"}, ctx)

            assert result["exit_code"] == 0
            cmd = mock_run.call_args[0][0]
            expected = f"--allowedTools={','.join(_DEFAULT_ALLOWED_TOOLS)}"
            assert any(expected in str(arg) for arg in cmd)

    @pytest.mark.asyncio
    async def test_invoke_skip_permissions(self):
        """Verify --dangerously-skip-permissions is added when skip_permissions=True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({
                "instruction": "test",
                "skip_permissions": True,
            }, ctx)

            assert result["exit_code"] == 0
            cmd = mock_run.call_args[0][0]
            assert "--dangerously-skip-permissions" in cmd

    @pytest.mark.asyncio
    async def test_invoke_skip_permissions_false(self):
        """Verify --dangerously-skip-permissions is NOT added when skip_permissions=False."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({
                "instruction": "test",
                "skip_permissions": False,
            }, ctx)

            assert result["exit_code"] == 0
            cmd = mock_run.call_args[0][0]
            assert "--dangerously-skip-permissions" not in cmd

    @pytest.mark.asyncio
    async def test_invoke_with_files(self):
        """Verify files are passed as positional arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({
                "instruction": "review these files",
                "files": ["/tmp/file1.py", "/tmp/file2.py"],
            }, ctx)

            assert result["exit_code"] == 0
            cmd = mock_run.call_args[0][0]
            # Files should be appended after the instruction
            assert "review these files" in cmd
            # Files that don't exist are silently skipped
            assert "/tmp/file1.py" not in cmd
            assert "/tmp/file2.py" not in cmd

    @pytest.mark.asyncio
    async def test_invoke_missing_instruction_raises(self):
        """Empty instruction should raise ToolError."""
        with patch.object(shutil, "which", return_value="/usr/local/bin/claude"):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            with pytest.raises(ToolError, match="instruction"):
                await tool.invoke({"instruction": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_subprocess_timeout(self):
        """Subprocess timeout should raise ToolError with output."""
        exc = subprocess.TimeoutExpired(
            cmd=["claude", "-p", "test"],
            timeout=10,
        )
        exc.stdout = b"partial output"
        exc.stderr = b"partial error"

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", side_effect=exc),
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            with pytest.raises(ToolError, match="timed out"):
                await tool.invoke({"instruction": "test", "timeout": 10}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_output_truncation(self):
        """Very long output should be truncated."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "x" * 10_000
        mock_result.stderr = ""

        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            tool = RunClaudeCodeTool()
            ctx = MockContext()
            result = await tool.invoke({"instruction": "test"}, ctx)

            assert result["exit_code"] == 0
            assert "truncated" in result["output"]
