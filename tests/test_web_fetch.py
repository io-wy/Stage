"""Tests for web_fetch tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from openagents.errors.exceptions import ToolError

from openagents_orchestration.tools.corecoder.web_fetch import WebFetchTool


def _make_response(body: bytes, content_type: str = "text/html; charset=utf-8") -> MagicMock:
    response = MagicMock()
    response.headers = {"Content-Type": content_type}
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    return response


@pytest.mark.asyncio
async def test_web_fetch_html_converts_to_markdown():
    html = """
    <html>
      <body>
        <h1>Hello World</h1>
        <p>This is a <strong>test</strong> page.</p>
      </body>
    </html>
    """
    tool = WebFetchTool()
    with patch("urllib.request.urlopen", return_value=_make_response(html.encode("utf-8"))):
        result = await tool.invoke({"url": "https://example.com"}, None)

    assert "Hello World" in result["content"]
    assert "test" in result["content"]
    assert result["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_web_fetch_anchor_link_text():
    html = """
    <html><body>
      <p>Visit <a href="https://example.com">example site</a> now.</p>
    </body></html>
    """
    tool = WebFetchTool()
    with patch("urllib.request.urlopen", return_value=_make_response(html.encode("utf-8"))):
        result = await tool.invoke({"url": "https://example.com"}, None)

    assert "[example site](https://example.com)" in result["content"]
    assert "[https://example.com](https://example.com)" not in result["content"]


@pytest.mark.asyncio
async def test_web_fetch_truncates_long_content():
    html = f"<html><body><p>{'x' * 10_000}</p></body></html>"
    tool = WebFetchTool()
    with patch("urllib.request.urlopen", return_value=_make_response(html.encode("utf-8"))):
        result = await tool.invoke({"url": "https://example.com", "max_chars": 500}, None)

    assert len(result["content"]) < 600
    assert "truncated" in result["content"]


@pytest.mark.asyncio
async def test_web_fetch_missing_url():
    tool = WebFetchTool()
    with pytest.raises(ToolError, match="url is required"):
        await tool.invoke({"url": ""}, None)


def test_schema_and_spec():
    tool = WebFetchTool()
    spec = tool.execution_spec()
    assert spec.side_effects == "none"
    schema = tool.schema()
    assert "url" in schema["properties"]
    assert schema["required"] == ["url"]
