"""Tests for RedisMailbox with a mocked Redis client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.mailbox.redis import (
    REDIS_AVAILABLE,
    RedisMailbox,
    StructuredMessage,
)


@pytest.fixture
def fake_redis():
    """Mock redis client with Stream API."""
    client = MagicMock()
    client.xlen = AsyncMock(return_value=0)
    client.xadd = AsyncMock(return_value="123-0")
    client.xgroup_create = AsyncMock()
    client.xreadgroup = AsyncMock(return_value=[])
    client.xrevrange = AsyncMock(return_value=[])
    client.xrange = AsyncMock(return_value=[])
    client.xack = AsyncMock()
    client.xdel = AsyncMock()
    client.xpending_range = AsyncMock(return_value=[])
    return client


@pytest.mark.skipif(not REDIS_AVAILABLE, reason="redis package not installed")
class TestRedisMailbox:
    async def test_enqueue_basic(self, fake_redis):
        mbox = RedisMailbox(fake_redis, "agent-1")
        msg = StructuredMessage.from_text("a", "agent-1", "hello")
        ok = await mbox.enqueue(msg)
        assert ok is True
        fake_redis.xadd.assert_awaited_once()

    async def test_enqueue_back_pressure(self, fake_redis):
        fake_redis.xlen = AsyncMock(return_value=10_000)
        mbox = RedisMailbox(fake_redis, "agent-1", max_size=10_000)
        msg = StructuredMessage.from_text("a", "agent-1", "hello")
        ok = await mbox.enqueue(msg)
        assert ok is False

    async def test_dequeue_empty(self, fake_redis):
        mbox = RedisMailbox(fake_redis, "agent-1")
        msgs = await mbox.dequeue(batch_size=5)
        assert msgs == []

    async def test_dequeue_with_message(self, fake_redis):
        msg = StructuredMessage.from_text("a", "agent-1", "hello")
        encoded = msg.to_dict()
        fake_redis.xreadgroup = AsyncMock(
            return_value=[
                (
                    "mailbox:agent-1",
                    [("123-0", {"data": __import__("json").dumps(encoded)})],
                )
            ]
        )
        mbox = RedisMailbox(fake_redis, "agent-1")
        msgs = await mbox.dequeue(batch_size=5)
        assert len(msgs) == 1
        assert msgs[0].text == "hello"
        assert msgs[0].header.delivery_count == 1

    async def test_ack(self, fake_redis):
        mbox = RedisMailbox(fake_redis, "agent-1")
        await mbox.ack("123-0")
        fake_redis.xack.assert_awaited_once_with(
            "mailbox:agent-1", "cg:agent-1", "123-0"
        )

    async def test_peek(self, fake_redis):
        msg = StructuredMessage.from_text("a", "agent-1", "peek")
        encoded = msg.to_dict()
        fake_redis.xrevrange = AsyncMock(
            return_value=[("123-0", {"data": __import__("json").dumps(encoded)})]
        )
        mbox = RedisMailbox(fake_redis, "agent-1")
        msgs = await mbox.peek(limit=1)
        assert len(msgs) == 1
        assert msgs[0].text == "peek"

    async def test_expired_enqueue_dropped(self, fake_redis):
        from datetime import datetime, timedelta, timezone

        msg = StructuredMessage(
            header=__import__("openagents_orchestration.models.message", fromlist=["MessageHeader"]).MessageHeader(
                sender="a",
                recipient="agent-1",
                created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
                ttl_s=1.0,
            ),
            text="old",
        )
        mbox = RedisMailbox(fake_redis, "agent-1")
        ok = await mbox.enqueue(msg)
        assert ok is True  # Silently dropped
        fake_redis.xadd.assert_not_awaited()
