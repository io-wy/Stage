"""MCP client integration for CoreCoder.

Provides a thin manager around the official MCP Python SDK so that MCP servers
can be declared in agent.json and their tools injected into the CoreCoder tool
pool at runtime.

Supported transports:
- stdio (current iteration)
- sse / streamable_http (future)
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ReadResourceResult,
    Tool,
)


@dataclass
class McpServerConfig:
    """Normalized configuration for one MCP server connection."""

    name: str
    transport: str  # "stdio" | "sse" | "streamable_http"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None


class McpClientManager:
    """Manages the lifecycle of one or more MCP server connections.

    Usage:
        manager = McpClientManager({"filesystem": {...}})
        await manager.connect_all()
        tools = await manager.list_tools("filesystem")
        result = await manager.call_tool("filesystem", "read_file", {"path": "/tmp/x"})
        await manager.close()
    """

    def __init__(self, servers: dict[str, dict[str, Any]] | None = None):
        self._raw_configs = servers or {}
        self._configs: dict[str, McpServerConfig] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stacks: dict[str, contextlib.AsyncExitStack] = {}
        self._tools: dict[str, list[Tool]] = {}
        self._resources: dict[str, list[Any]] = {}
        self._prompts: dict[str, list[Any]] = {}
        self._parse_configs()

    def _parse_configs(self) -> None:
        for name, cfg in self._raw_configs.items():
            transport = str(cfg.get("transport", "stdio")).lower()
            self._configs[name] = McpServerConfig(
                name=name,
                transport=transport,
                command=cfg.get("command"),
                args=cfg.get("args") or [],
                env=cfg.get("env") or {},
                url=cfg.get("url"),
            )

    async def connect_all(self) -> dict[str, str]:
        """Connect to all configured servers. Returns {name: error_or_ok}."""
        results: dict[str, str] = {}
        for name in self._configs:
            try:
                await self._connect(name)
                results[name] = "ok"
            except Exception as exc:  # pragma: no cover - logged, not fatal
                results[name] = f"failed: {exc}"
        return results

    async def _connect(self, name: str) -> None:
        cfg = self._configs[name]
        if cfg.transport == "stdio":
            await self._connect_stdio(name, cfg)
        elif cfg.transport == "sse":
            await self._connect_sse(name, cfg)
        else:
            raise NotImplementedError(f"MCP transport '{cfg.transport}' not yet supported")

    async def _connect_stdio(self, name: str, cfg: McpServerConfig) -> None:
        if not cfg.command:
            raise ValueError(f"MCP server '{name}' missing command")

        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args or [],
            env=cfg.env,
        )
        stack = contextlib.AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._exit_stacks[name] = stack
        self._sessions[name] = session

    async def _connect_sse(self, name: str, cfg: McpServerConfig) -> None:
        if not cfg.url:
            raise ValueError(f"MCP server '{name}' missing url")

        stack = contextlib.AsyncExitStack()
        read, write = await stack.enter_async_context(sse_client(cfg.url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._exit_stacks[name] = stack
        self._sessions[name] = session

    async def list_tools(self, server_name: str | None = None) -> dict[str, list[Tool]]:
        """List tools from one or all connected servers.

        Returns {server_name: [Tool, ...]}.
        """
        if server_name is not None:
            session = self._sessions.get(server_name)
            if session is None:
                return {}
            result = await session.list_tools()
            self._tools[server_name] = list(result.tools)
            return {server_name: list(result.tools)}

        out: dict[str, list[Tool]] = {}
        for name, session in self._sessions.items():
            result = await session.list_tools()
            self._tools[name] = list(result.tools)
            out[name] = list(result.tools)
        return out

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        """Call a tool on the named server."""
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP server '{server_name}' is not connected")
        return await session.call_tool(tool_name, arguments=arguments)

    async def list_resources(self, server_name: str | None = None) -> dict[str, list[Any]]:
        """List resources from one or all connected servers.

        Returns {server_name: [Resource, ...]}.
        """
        if server_name is not None:
            session = self._sessions.get(server_name)
            if session is None:
                return {}
            result: ListResourcesResult = await session.list_resources()
            self._resources[server_name] = list(result.resources)
            return {server_name: list(result.resources)}

        out: dict[str, list[Any]] = {}
        for name, session in self._sessions.items():
            result = await session.list_resources()
            self._resources[name] = list(result.resources)
            out[name] = list(result.resources)
        return out

    async def read_resource(self, server_name: str, uri: str) -> ReadResourceResult:
        """Read a resource URI from the named server."""
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP server '{server_name}' is not connected")
        return await session.read_resource(uri)

    async def list_prompts(self, server_name: str | None = None) -> dict[str, list[Any]]:
        """List prompts from one or all connected servers.

        Returns {server_name: [Prompt, ...]}.
        """
        if server_name is not None:
            session = self._sessions.get(server_name)
            if session is None:
                return {}
            result: ListPromptsResult = await session.list_prompts()
            self._prompts[server_name] = list(result.prompts)
            return {server_name: list(result.prompts)}

        out: dict[str, list[Any]] = {}
        for name, session in self._sessions.items():
            result = await session.list_prompts()
            self._prompts[name] = list(result.prompts)
            out[name] = list(result.prompts)
        return out

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
    ) -> GetPromptResult:
        """Get a rendered prompt from the named server."""
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP server '{server_name}' is not connected")
        return await session.get_prompt(prompt_name, arguments=arguments or {})

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Return the server name that owns tool_name, or None."""
        for server_name, tools in self._tools.items():
            for tool in tools:
                if tool.name == tool_name:
                    return server_name
        return None

    async def close(self) -> None:
        """Close all connections."""
        for stack in self._exit_stacks.values():
            await stack.aclose()
        self._exit_stacks.clear()
        self._sessions.clear()
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()

    async def __aenter__(self) -> McpClientManager:
        await self.connect_all()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
