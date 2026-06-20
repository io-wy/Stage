"""Test real side effects of write_file, edit_file, and bash tools.

No filesystem or subprocess mocking — every test touches the real OS.
All writes are sandboxed inside a tmp_path created by pytest.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from openagents.errors.exceptions import ModelRetryError, ToolError
from openagents_orchestration.tools.corecoder.write_file import WriteFileTool
from openagents_orchestration.tools.corecoder.edit_file import EditFileTool
from openagents_orchestration.tools.corecoder.bash_tool import BashTool


def _make_context(tmp_path, agent_id="test-agent"):
    """Build a minimal RunContext-like object with scratch and runner._current_work_dir."""
    runner = MagicMock()
    runner._current_work_dir = str(tmp_path)
    deps = MagicMock()
    deps.runner = runner
    deps.artifact_store = None
    ctx = MagicMock()
    ctx.agent_id = agent_id
    ctx.deps = deps
    ctx.scratch = {}
    return ctx


# ─────────────────────────── WriteFileTool ───────────────────────────

class TestWriteFileTool:
    @pytest.mark.asyncio
    async def test_write_creates_file_and_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = WriteFileTool()

        result = await tool.invoke(
            {"file_path": "src/foo.py", "content": "print('hello')\n"}, ctx
        )

        written = tmp_path / "src" / "foo.py"
        assert written.exists()
        assert written.read_text() == "print('hello')\n"
        assert result["file_path"] == str(written)
        assert result["lines_written"] == 1

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = WriteFileTool()

        await tool.invoke(
            {"file_path": "bar.py", "content": "old content"}, ctx
        )
        await tool.invoke(
            {"file_path": "bar.py", "content": "new content"}, ctx
        )

        assert (tmp_path / "bar.py").read_text() == "new content"

    @pytest.mark.asyncio
    async def test_write_empty_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = WriteFileTool()

        result = await tool.invoke(
            {"file_path": "empty.txt", "content": ""}, ctx
        )

        path = tmp_path / "empty.txt"
        assert path.exists()
        assert path.stat().st_size == 0
        assert result["bytes_written"] == 0

    @pytest.mark.asyncio
    async def test_write_records_dirty_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = WriteFileTool()

        await tool.invoke(
            {"file_path": "a.py", "content": "x = 1\n"}, ctx
        )

        dirty = ctx.scratch["dirty_files"]
        assert isinstance(dirty, set)
        assert str((tmp_path / "a.py").resolve()) in dirty


# ─────────────────────────── EditFileTool ───────────────────────────

class TestEditFileTool:
    @pytest.mark.asyncio
    async def test_edit_replaces_exact_match(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = EditFileTool()

        (tmp_path / "hello.txt").write_text("hello world")

        result = await tool.invoke(
            {"file_path": "hello.txt", "old_string": "hello", "new_string": "hi"}, ctx
        )

        assert (tmp_path / "hello.txt").read_text() == "hi world"
        assert "hi world" in result["diff"]

    @pytest.mark.asyncio
    async def test_edit_no_match_leaves_file_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = EditFileTool()

        (tmp_path / "hello.txt").write_text("hello world")

        with pytest.raises(ModelRetryError):
            await tool.invoke(
                {"file_path": "hello.txt", "old_string": "goodbye", "new_string": "hi"}, ctx
            )

        assert (tmp_path / "hello.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_edit_multiple_matches_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = EditFileTool()

        (tmp_path / "dup.txt").write_text("abc abc abc")

        with pytest.raises(ModelRetryError) as exc_info:
            await tool.invoke(
                {"file_path": "dup.txt", "old_string": "abc", "new_string": "xyz"}, ctx
            )

        assert "3 times" in str(exc_info.value)
        assert (tmp_path / "dup.txt").read_text() == "abc abc abc"

    @pytest.mark.asyncio
    async def test_edit_missing_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = EditFileTool()

        with pytest.raises(ToolError) as exc_info:
            await tool.invoke(
                {"file_path": "missing.txt", "old_string": "a", "new_string": "b"}, ctx
            )

        assert "not found" in str(exc_info.value).lower()


# ─────────────────────────── BashTool ───────────────────────────

class TestBashTool:
    @pytest.mark.asyncio
    async def test_bash_runs_simple_command(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = BashTool()

        result = await tool.invoke({"command": "echo hello"}, ctx)

        assert result["blocked"] is False
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_bash_blocked_dangerous(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = BashTool()

        result = await tool.invoke({"command": "rm -rf /"}, ctx)

        assert result["blocked"] is True
        assert result["exit_code"] is None
        assert result["stdout"] == ""

    @pytest.mark.asyncio
    async def test_bash_permission_required(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = BashTool()

        result = await tool.invoke({"command": "rm -rf build/"}, ctx)

        assert result["blocked"] is False
        assert result["requires_permission"] is True
        assert result["executed"] is False

    @pytest.mark.asyncio
    async def test_bash_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = BashTool()

        result = await tool.invoke({"command": "sleep 10", "timeout": 1}, ctx)

        assert result["timed_out"] is True
        assert result["exit_code"] is None

    @pytest.mark.asyncio
    async def test_bash_cwd_tracking(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = _make_context(tmp_path)
        tool = BashTool()

        # Create a subdirectory
        (tmp_path / "subdir").mkdir()

        # cd into subdir
        await tool.invoke({"command": "cd subdir"}, ctx)
        assert ctx.scratch["bash_cwd"] == str(tmp_path / "subdir")

        # Run pwd in the tracked cwd
        result = await tool.invoke({"command": "pwd"}, ctx)
        assert result["stdout"].strip() == str(tmp_path / "subdir")
