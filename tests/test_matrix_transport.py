"""Tests for Matrix transport layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.transport.matrix_transport import (
    MatrixClient,
    MatrixConfig,
    MatrixTransport,
)


class _FakeRoomCreateResponse:
    room_id = "!room:example.com"


class _FakeSendResponse:
    event_id = "$event:example.com"


@pytest.fixture
def mock_matrix_client():
    client = MagicMock(spec=MatrixClient)
    client.user_id = "@director:example.com"
    return client


@pytest.mark.asyncio
async def test_matrix_transport_disabled_when_no_client():
    transport = MatrixTransport()
    assert transport.enabled is False
    assert await transport.create_room("test") == ""
    assert await transport.send("!room:example.com", "hi") == ""
    assert await transport.receive() == []


@pytest.mark.asyncio
async def test_matrix_transport_create_room(mock_matrix_client):
    mock_matrix_client.ensure_room = AsyncMock(return_value="!room:example.com")
    transport = MatrixTransport(mock_matrix_client)
    assert transport.enabled is True
    rid = await transport.create_room("leader-room", invite=["@worker:example.com"])
    assert rid == "!room:example.com"
    mock_matrix_client.ensure_room.assert_awaited_once_with("leader-room", invite=["@worker:example.com"])


@pytest.mark.asyncio
async def test_matrix_transport_send(mock_matrix_client):
    mock_matrix_client.send_text = AsyncMock(return_value="$event:example.com")
    transport = MatrixTransport(mock_matrix_client)
    event_id = await transport.send("!room:example.com", "hello")
    assert event_id == "$event:example.com"
    mock_matrix_client.send_text.assert_awaited_once_with("!room:example.com", "hello")


@pytest.mark.asyncio
async def test_matrix_transport_receive(mock_matrix_client):
    mock_matrix_client.sync = AsyncMock(
        return_value=[
            {"room_id": "!room:example.com", "sender": "@worker:example.com", "body": "done", "event_id": "$e1", "ts": 1.0},
        ]
    )
    transport = MatrixTransport(mock_matrix_client)
    msgs = await transport.receive()
    assert len(msgs) == 1
    assert msgs[0]["body"] == "done"
    mock_matrix_client.sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_matrix_transport_close(mock_matrix_client):
    mock_matrix_client.close = AsyncMock()
    transport = MatrixTransport(mock_matrix_client)
    await transport.close()
    mock_matrix_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_matrix_transport_per_agent_client():
    primary = MagicMock(spec=MatrixClient)
    primary.ensure_room = AsyncMock(return_value="!primary:example.com")
    per_agent = MagicMock(spec=MatrixClient)
    per_agent.ensure_room = AsyncMock(return_value="!agent:example.com")

    transport = MatrixTransport(primary)
    transport._clients["coder-t1"] = per_agent

    # Primary client for unknown agent
    rid = await transport.create_room("dm", agent_id="unknown")
    assert rid == "!primary:example.com"

    # Per-agent client for registered agent
    rid = await transport.create_room("dm", agent_id="coder-t1")
    assert rid == "!agent:example.com"
    per_agent.ensure_room.assert_awaited_once()
