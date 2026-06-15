"""Tests for list_directory tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openagents.interfaces.run_context import RunContext, RunUsage

from openagents_orchestration.tools.corecoder.list_directory import ListDirectoryTool


def _make_context(cwd: str | None = None) -> RunContext[Any]:
    scratch: dict[str, Any] = {}
    if cwd:
        scratch["bash_cwd"] = cwd
    return RunContext(
        agent_id="test",
        session_id="test",
        run_id="r1",
        input_text="hi",
        event_bus=object(),
        state={},
        scratch=scratch,
        system_prompt_fragments=[],
        usage=RunUsage(),
        tool_results=[],
    )


@pytest.mark.asyncio
async def test_list_directory_basic(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "c.py").write_text("y")

    tool = ListDirectoryTool()
    ctx = _make_context(str(tmp_path))
    result = await tool.invoke({"path": str(tmp_path), "depth": 1}, ctx)

    assert result["path"] == str(tmp_path)
    names = {e["name"] for e in result["entries"]}
    assert "a.py" in names
    assert "b" in names
    assert "c.py" in names  # depth 1 includes one level below root
    assert "📁 b" in result["message"]


@pytest.mark.asyncio
async def test_list_directory_recursive(tmp_path: Path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "nested.py").write_text("x")

    tool = ListDirectoryTool()
    ctx = _make_context(str(tmp_path))
    result = await tool.invoke({"path": str(tmp_path), "depth": 2}, ctx)

    names = {e["name"] for e in result["entries"]}
    assert "dir" in names
    assert "nested.py" in names


@pytest.mark.asyncio
async def test_list_directory_missing_path():
    tool = ListDirectoryTool()
    ctx = _make_context()
    with pytest.raises(Exception, match="Path not found"):
        await tool.invoke({"path": "/nonexistent/path"}, ctx)


@pytest.mark.asyncio
async def test_list_directory_defaults_to_cwd(tmp_path: Path, monkeypatch: Any):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "file.py").write_text("x")

    tool = ListDirectoryTool()
    ctx = _make_context(str(tmp_path))
    result = await tool.invoke({}, ctx)

    assert result["path"] == str(tmp_path)
    assert any(e["name"] == "file.py" for e in result["entries"])


def test_schema_and_spec():
    tool = ListDirectoryTool()
    spec = tool.execution_spec()
    assert spec.side_effects == "readonly"
    schema = tool.schema()
    assert "path" in schema["properties"]
    assert "depth" in schema["properties"]
