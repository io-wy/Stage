"""Adapter that exposes MCP tools through the CoreCoder ToolPlugin interface."""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult, Tool
from openagents.errors.exceptions import ToolError
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin

from openagents_orchestration.tools.mcp.client import McpClientManager


def _mcp_type_to_json_schema(mcp_type: str) -> dict[str, Any]:
    """Best-effort mapping of MCP primitive types to JSON schema types."""
    mapping: dict[str, dict[str, Any]] = {
        "string": {"type": "string"},
        "integer": {"type": "integer"},
        "number": {"type": "number"},
        "boolean": {"type": "boolean"},
        "array": {"type": "array"},
        "object": {"type": "object"},
    }
    return mapping.get(mcp_type, {"type": "string"})


def _build_schema(tool: Tool) -> dict[str, Any]:
    """Build a JSON schema dict from an MCP Tool definition."""
    input_schema = tool.inputSchema or {}
    properties: dict[str, Any] = {}
    required: list[str] = []

    schema_properties = input_schema.get("properties") or {}
    schema_required = input_schema.get("required") or []

    for name, prop in schema_properties.items():
        if isinstance(prop, dict):
            prop_schema = dict(prop)
            # Ensure every property has a type for the model.
            if "type" not in prop_schema:
                prop_schema["type"] = "string"
            properties[name] = prop_schema
        else:
            properties[name] = {"type": "string"}

    if isinstance(schema_required, list):
        required = [str(r) for r in schema_required]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _format_result(result: CallToolResult) -> dict[str, Any]:
    """Convert an MCP CallToolResult into a CoreCoder tool result dict."""
    content_parts: list[str] = []
    is_error = result.isError or False
    for item in result.content:
        if getattr(item, "type", None) == "text":
            text = getattr(item, "text", "")
            if isinstance(text, str):
                content_parts.append(text)
        else:
            content_parts.append(str(item))

    text = "\n".join(content_parts)
    return {
        "message": text,
        "is_error": is_error,
        "content": result.content,
    }


class McpToolAdapter(ToolPlugin):
    """Wraps one MCP tool so it can be registered in a CoreCoder tool pool."""

    durable_idempotent = False

    def __init__(
        self,
        server_name: str,
        tool: Tool,
        manager: McpClientManager,
    ):
        self.server_name = server_name
        self.mcp_tool = tool
        self.manager = manager
        self.name = f"mcp:{server_name}:{tool.name}"
        self.description = tool.description or f"MCP tool '{tool.name}' from server '{server_name}'"

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(
            concurrency_safe=False,  # MCP tools may have server-side state
            side_effects="unknown",
            default_timeout_ms=60_000,
        )

    def schema(self) -> dict[str, Any]:
        return _build_schema(self.mcp_tool)

    async def invoke(
        self, params: dict[str, Any], context: RunContext[Any] | None
    ) -> dict[str, Any]:
        try:
            result = await self.manager.call_tool(
                self.server_name,
                self.mcp_tool.name,
                arguments=params,
            )
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"MCP tool '{self.mcp_tool.name}' failed: {exc}",
                tool_name=self.name,
            ) from exc

        formatted = _format_result(result)
        if formatted.get("is_error"):
            raise ToolError(
                formatted.get("message") or "MCP tool returned an error",
                tool_name=self.name,
            )
        return formatted


def build_mcp_tools(
    manager: McpClientManager,
    server_name: str,
    tools: list[Tool],
) -> list[McpToolAdapter]:
    """Build a list of McpToolAdapter instances for one server's tools."""
    return [McpToolAdapter(server_name, tool, manager) for tool in tools]
