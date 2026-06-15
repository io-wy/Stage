"""Tests for MCP client integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagents_orchestration.tools.mcp.adapter import (
    McpToolAdapter,
    _build_schema,
    _format_result,
)
from openagents_orchestration.tools.mcp.client import McpClientManager


def _mock_tool(name: str = "read_file", schema: dict | None = None) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock tool {name}"
    tool.inputSchema = schema or {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file path"},
        },
        "required": ["path"],
    }
    return tool


@pytest.mark.asyncio
async def test_manager_connects_stdio_and_lists_tools() -> None:
    manager = McpClientManager({
        "fs": {"command": "python", "args": ["server.py"]}
    })

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[_mock_tool("read_file")]))

    mock_stdio = MagicMock()
    mock_stdio.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_stdio.__aexit__ = AsyncMock(return_value=False)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("openagents_orchestration.tools.mcp.client.stdio_client", return_value=mock_stdio),
        patch("openagents_orchestration.tools.mcp.client.ClientSession", return_value=mock_session_ctx),
    ):
        results = await manager.connect_all()

    assert results == {"fs": "ok"}
    tools = await manager.list_tools("fs")
    assert "fs" in tools
    assert len(tools["fs"]) == 1
    assert tools["fs"][0].name == "read_file"
    await manager.close()


@pytest.mark.asyncio
async def test_manager_call_tool_forwards_arguments() -> None:
    manager = McpClientManager({
        "fs": {"command": "python", "args": ["server.py"]}
    })

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [MagicMock(type="text", text="hello")]
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    mock_stdio = MagicMock()
    mock_stdio.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_stdio.__aexit__ = AsyncMock(return_value=False)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("openagents_orchestration.tools.mcp.client.stdio_client", return_value=mock_stdio),
        patch("openagents_orchestration.tools.mcp.client.ClientSession", return_value=mock_session_ctx),
    ):
        await manager.connect_all()

    result = await manager.call_tool("fs", "read_file", {"path": "/tmp/x"})
    mock_session.call_tool.assert_awaited_once_with("read_file", arguments={"path": "/tmp/x"})
    assert result is mock_result
    await manager.close()


@pytest.mark.asyncio
async def test_manager_connects_sse_and_lists_tools() -> None:
    manager = McpClientManager({
        "remote": {"transport": "sse", "url": "http://localhost:3000/sse"}
    })

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[_mock_tool("fetch")]))

    mock_sse = MagicMock()
    mock_sse.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_sse.__aexit__ = AsyncMock(return_value=False)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("openagents_orchestration.tools.mcp.client.sse_client", return_value=mock_sse),
        patch("openagents_orchestration.tools.mcp.client.ClientSession", return_value=mock_session_ctx),
    ):
        results = await manager.connect_all()

    assert results == {"remote": "ok"}
    tools = await manager.list_tools("remote")
    assert "remote" in tools
    assert len(tools["remote"]) == 1
    assert tools["remote"][0].name == "fetch"
    await manager.close()


def test_build_schema_preserves_required_and_types() -> None:
    tool = _mock_tool(
        "add",
        schema={
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    )
    schema = _build_schema(tool)
    assert schema["type"] == "object"
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "integer"
    assert set(schema["required"]) == {"a", "b"}


def test_format_result_extracts_text_content() -> None:
    result = MagicMock()
    result.isError = False
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Tool output"
    result.content = [text_block]
    formatted = _format_result(result)
    assert formatted["message"] == "Tool output"
    assert formatted["is_error"] is False


@pytest.mark.asyncio
async def test_adapter_invoke_forwards_and_formats() -> None:
    manager = MagicMock()
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [MagicMock(type="text", text="done")]
    manager.call_tool = AsyncMock(return_value=mock_result)

    tool = _mock_tool("read_file")
    adapter = McpToolAdapter("fs", tool, manager)

    assert adapter.name == "mcp:fs:read_file"
    result = await adapter.invoke({"path": "/tmp/x"}, None)
    manager.call_tool.assert_awaited_once_with("fs", "read_file", arguments={"path": "/tmp/x"})
    assert result["message"] == "done"


@pytest.mark.asyncio
async def test_adapter_invoke_raises_on_error_result() -> None:
    from openagents.errors.exceptions import ToolError

    manager = MagicMock()
    mock_result = MagicMock()
    mock_result.isError = True
    mock_result.content = [MagicMock(type="text", text="boom")]
    manager.call_tool = AsyncMock(return_value=mock_result)

    tool = _mock_tool("read_file")
    adapter = McpToolAdapter("fs", tool, manager)

    with pytest.raises(ToolError):
        await adapter.invoke({"path": "/tmp/x"}, None)


@pytest.mark.asyncio
async def test_manager_lists_and_reads_resources() -> None:
    manager = McpClientManager({
        "fs": {"command": "python", "args": ["server.py"]}
    })

    mock_resource = MagicMock()
    mock_resource.uri = "file:///tmp/x"
    mock_resource.name = "x"

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[mock_resource]))
    mock_read_result = MagicMock()
    mock_read_result.contents = [MagicMock(type="text", text="resource content")]
    mock_session.read_resource = AsyncMock(return_value=mock_read_result)

    mock_stdio = MagicMock()
    mock_stdio.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_stdio.__aexit__ = AsyncMock(return_value=False)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("openagents_orchestration.tools.mcp.client.stdio_client", return_value=mock_stdio),
        patch("openagents_orchestration.tools.mcp.client.ClientSession", return_value=mock_session_ctx),
    ):
        await manager.connect_all()

    resources = await manager.list_resources("fs")
    assert resources["fs"][0].uri == "file:///tmp/x"

    result = await manager.read_resource("fs", "file:///tmp/x")
    mock_session.read_resource.assert_awaited_once_with("file:///tmp/x")
    assert result is mock_read_result
    await manager.close()


@pytest.mark.asyncio
async def test_manager_lists_and_gets_prompts() -> None:
    manager = McpClientManager({
        "fs": {"command": "python", "args": ["server.py"]}
    })

    mock_prompt = MagicMock()
    mock_prompt.name = "review_code"

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[mock_prompt]))
    mock_prompt_result = MagicMock()
    mock_prompt_result.messages = [MagicMock(role="user", content=MagicMock(type="text", text="review this"))]
    mock_session.get_prompt = AsyncMock(return_value=mock_prompt_result)

    mock_stdio = MagicMock()
    mock_stdio.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_stdio.__aexit__ = AsyncMock(return_value=False)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("openagents_orchestration.tools.mcp.client.stdio_client", return_value=mock_stdio),
        patch("openagents_orchestration.tools.mcp.client.ClientSession", return_value=mock_session_ctx),
    ):
        await manager.connect_all()

    prompts = await manager.list_prompts("fs")
    assert prompts["fs"][0].name == "review_code"

    result = await manager.get_prompt("fs", "review_code", {"language": "python"})
    mock_session.get_prompt.assert_awaited_once_with("review_code", arguments={"language": "python"})
    assert result is mock_prompt_result
    await manager.close()
