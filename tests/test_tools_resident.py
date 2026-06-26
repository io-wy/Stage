
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from openagents.errors.exceptions import PermanentToolError

from openagents_orchestration.tools.resident.send_to_resident import SendToResidentTool
from openagents_orchestration.tools.resident.spawn_resident import SpawnResidentTool
from openagents_orchestration.tools.resident.stop_resident import StopResidentTool


class MockContext:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# SpawnResidentTool
# ---------------------------------------------------------------------------

class TestSpawnResidentTool:
    def test_schema_has_agent_type(self):
        tool = SpawnResidentTool()
        schema = tool.schema()
        assert "agent_type" in schema["properties"]
        assert schema.get("required") == ["agent_type"]

    @pytest.mark.asyncio
    async def test_invoke_creates_resident(self):
        mock_runner = MagicMock()
        mock_runner.start_resident = AsyncMock(return_value="coder-abc123")

        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = SpawnResidentTool()
        result = await tool.invoke({"agent_type": "coder"}, ctx)

        assert result["resident_id"] == "coder-abc123"
        assert result["agent_type"] == "coder"
        assert result["status"] == "started"
        mock_runner.start_resident.assert_awaited_once_with("coder")

    @pytest.mark.asyncio
    async def test_invoke_missing_agent_type_raises(self):
        mock_runner = MagicMock()
        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = SpawnResidentTool()
        with pytest.raises(PermanentToolError, match="agent_type"):
            await tool.invoke({"agent_type": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_runner_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = SpawnResidentTool()
        with pytest.raises(PermanentToolError, match="Runner"):
            await tool.invoke({"agent_type": "coder"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_start_resident_failure_raises(self):
        mock_runner = MagicMock()
        mock_runner.start_resident = AsyncMock(side_effect=RuntimeError("OOM"))

        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = SpawnResidentTool()
        with pytest.raises(PermanentToolError, match="Failed to start"):
            await tool.invoke({"agent_type": "coder"}, ctx)


# ---------------------------------------------------------------------------
# StopResidentTool
# ---------------------------------------------------------------------------

class TestStopResidentTool:
    def test_schema_has_resident_id(self):
        tool = StopResidentTool()
        schema = tool.schema()
        assert "resident_id" in schema["properties"]
        assert schema.get("required") == ["resident_id"]

    @pytest.mark.asyncio
    async def test_invoke_stops_resident(self):
        mock_runner = MagicMock()
        mock_runner.stop_resident = AsyncMock()

        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = StopResidentTool()
        result = await tool.invoke({"resident_id": "coder-abc123"}, ctx)

        assert result["resident_id"] == "coder-abc123"
        assert result["status"] == "stopped"
        mock_runner.stop_resident.assert_awaited_once_with("coder-abc123")

    @pytest.mark.asyncio
    async def test_invoke_missing_resident_id_raises(self):
        mock_runner = MagicMock()
        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = StopResidentTool()
        with pytest.raises(PermanentToolError, match="resident_id"):
            await tool.invoke({"resident_id": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_runner_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = StopResidentTool()
        with pytest.raises(PermanentToolError, match="Runner"):
            await tool.invoke({"resident_id": "coder-abc123"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_stop_resident_failure_raises(self):
        mock_runner = MagicMock()
        mock_runner.stop_resident = AsyncMock(side_effect=RuntimeError("connection lost"))

        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = StopResidentTool()
        with pytest.raises(PermanentToolError, match="Failed to stop"):
            await tool.invoke({"resident_id": "coder-abc123"}, ctx)


# ---------------------------------------------------------------------------
# SendToResidentTool
# ---------------------------------------------------------------------------

class TestSendToResidentTool:
    def test_schema_has_required_fields(self):
        tool = SendToResidentTool()
        schema = tool.schema()
        assert "resident_id" in schema["properties"]
        assert "task" in schema["properties"]
        assert "content" in schema["properties"]
        assert "context" in schema["properties"]
        assert schema.get("required") == ["resident_id", "task"]

    @pytest.mark.asyncio
    async def test_invoke_sends_message(self):
        mock_runner = MagicMock()
        mock_runner.send_to_resident = AsyncMock()

        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = SendToResidentTool()
        result = await tool.invoke({
            "resident_id": "coder-abc123",
            "task": "write a test",
            "content": "for the login module",
            "context": "file: tests/test_login.py",
        }, ctx)

        assert result["resident_id"] == "coder-abc123"
        assert result["status"] == "sent"
        mock_runner.send_to_resident.assert_awaited_once()
        # Verify the call args include from_id
        call_kwargs = mock_runner.send_to_resident.await_args[1]
        assert call_kwargs["from_id"] == "director"
        assert call_kwargs["task"] == "write a test"
        assert call_kwargs["content"] == "for the login module"
        assert call_kwargs["context"] == "file: tests/test_login.py"

    @pytest.mark.asyncio
    async def test_invoke_missing_params_raises(self):
        mock_runner = MagicMock()
        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = SendToResidentTool()
        with pytest.raises(PermanentToolError, match="required"):
            await tool.invoke({"resident_id": "", "task": ""}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_no_runner_raises(self):
        ctx = MockContext(deps=MockContext(), agent_id="director")
        tool = SendToResidentTool()
        with pytest.raises(PermanentToolError, match="Runner"):
            await tool.invoke({"resident_id": "x", "task": "y"}, ctx)

    @pytest.mark.asyncio
    async def test_invoke_send_failure_raises(self):
        mock_runner = MagicMock()
        mock_runner.send_to_resident = AsyncMock(side_effect=RuntimeError("queue full"))

        ctx = MockContext(deps=MockContext(runner=mock_runner), agent_id="director")
        tool = SendToResidentTool()
        with pytest.raises(PermanentToolError, match="Failed to send"):
            await tool.invoke({"resident_id": "x", "task": "y"}, ctx)


    def test_schema_has_resident_id(self):
        tool = SendToResidentTool()
        schema = tool.schema()
        assert "resident_id" in schema["properties"]
        assert schema.get("required") == ["resident_id", "task"]