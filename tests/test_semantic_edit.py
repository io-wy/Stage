"""Tests for semantic_edit tool."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from openagents.errors.exceptions import ModelRetryError, ToolError

from openagents_orchestration.tools.corecoder.semantic_edit import SemanticEditTool


class MockLLMResponse:
    def __init__(self, text: str):
        self.output_text = text
        self.usage = None


class MockContext:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.scratch: dict[str, object] = {}


class TestSemanticEditTool:
    def test_schema_and_spec(self):
        tool = SemanticEditTool()
        spec = tool.execution_spec()
        assert spec.concurrency_safe is False
        assert spec.side_effects == "writes_filesystem"
        schema = tool.schema()
        props = schema.get("properties", {})
        assert "file_path" in props
        assert "instruction" in props
        assert schema.get("required") == ["file_path", "instruction"]

    def test_invoke_missing_file_path(self):
        tool = SemanticEditTool()
        with pytest.raises(ToolError, match="file_path is required"):
            asyncio.run(tool.invoke({"instruction": "change something"}, None))

    def test_invoke_missing_instruction(self, tmp_path):
        tool = SemanticEditTool()
        with pytest.raises(ToolError, match="instruction is required"):
            asyncio.run(
                tool.invoke({"file_path": str(tmp_path / "x.py")}, None)
            )

    def test_invoke_file_not_found(self):
        tool = SemanticEditTool()
        with pytest.raises(ToolError, match="File not found"):
            asyncio.run(
                tool.invoke(
                    {"file_path": "/nonexistent/file.py", "instruction": "change"},
                    None,
                )
            )

    def test_invoke_no_llm_client(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        tool = SemanticEditTool()
        with pytest.raises(ModelRetryError, match="No LLM client"):
            asyncio.run(
                tool.invoke(
                    {"file_path": str(f), "instruction": "change x to 2"},
                    None,
                )
            )

    def test_invoke_code_block_output(self, tmp_path):
        f = tmp_path / "test.py"
        original = "x = 1\ny = 2\n"
        f.write_text(original, encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(
            return_value=MockLLMResponse(
                "```python\nx = 10\ny = 2\n```"
            )
        )

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        result = asyncio.run(
            tool.invoke(
                {"file_path": str(f), "instruction": "change x to 10"},
                ctx,
            )
        )

        assert result["changed"] is True
        assert "x = 10" in result["diff"]
        assert f.read_text(encoding="utf-8") == "x = 10\ny = 2"
        # Verify dirty_files tracking
        dirty = ctx.scratch.get("dirty_files")
        assert dirty is not None
        assert str(f.resolve()) in dirty

    def test_invoke_code_block_output_for_function(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(
            return_value=MockLLMResponse(
                "```python\ndef foo():\n    return 42\n```"
            )
        )

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        result = asyncio.run(
            tool.invoke(
                {"file_path": str(f), "instruction": "make foo return 42"},
                ctx,
            )
        )

        assert result["changed"] is True
        assert "return 42" in f.read_text(encoding="utf-8")

    def test_invoke_no_change(self, tmp_path):
        f = tmp_path / "test.py"
        original = "x = 1\n"
        f.write_text(original, encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(
            return_value=MockLLMResponse(
                "```python\nx = 1\n```"
            )
        )

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        result = asyncio.run(
            tool.invoke(
                {"file_path": str(f), "instruction": "do nothing"},
                ctx,
            )
        )

        assert result["changed"] is False
        assert "No changes" in result["message"]
        assert f.read_text(encoding="utf-8") == original

    def test_invoke_empty_llm_response(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(
            return_value=MockLLMResponse("")
        )

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        with pytest.raises(ModelRetryError, match="empty response"):
            asyncio.run(
                tool.invoke(
                    {"file_path": str(f), "instruction": "change"},
                    ctx,
                )
            )

    def test_invoke_llm_error(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(side_effect=RuntimeError("API down"))

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        with pytest.raises(ModelRetryError, match="LLM call failed"):
            asyncio.run(
                tool.invoke(
                    {"file_path": str(f), "instruction": "change"},
                    ctx,
                )
            )

    def test_invoke_empty_response(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(
            return_value=MockLLMResponse("   ")  # whitespace-only response
        )

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        with pytest.raises(ModelRetryError, match="empty response"):
            asyncio.run(
                tool.invoke(
                    {"file_path": str(f), "instruction": "change"},
                    ctx,
                )
            )

    def test_invoke_empty_modified_content(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        llm_client = AsyncMock()
        llm_client.generate = AsyncMock(
            return_value=MockLLMResponse("```python\n   \n```")
        )

        ctx = MockContext(llm_client=llm_client)
        tool = SemanticEditTool()
        with pytest.raises(ModelRetryError, match="empty modified_content"):
            asyncio.run(
                tool.invoke(
                    {"file_path": str(f), "instruction": "delete everything"},
                    ctx,
                )
            )
