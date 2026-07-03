"""Tests for MailboxManager lifecycle and resource cleanup."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openagents_orchestration.core.mailbox_manager import MailboxManager


@pytest.mark.asyncio
async def test_validate_redis_closes_client_on_fallback():
    """Regression: falling back to memory must close the aioredis connection."""
    manager = MailboxManager(mailbox_backend="memory")
    manager._mailbox_backend = "redis"  # pretend redis was initially configured
    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(side_effect=ConnectionError("boom"))
    manager._redis_client = fake_client

    ok = await manager.validate_redis()

    assert ok is False
    assert manager._mailbox_backend == "memory"
    assert manager._redis_client is None
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_closes_redis_client():
    manager = MailboxManager(mailbox_backend="memory")
    manager._mailbox_backend = "redis"
    fake_client = AsyncMock()
    manager._redis_client = fake_client

    await manager.close()

    assert manager._redis_client is None
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_is_safe_when_no_redis_client():
    manager = MailboxManager(mailbox_backend="memory")
    await manager.close()  # should not raise
    assert manager._redis_client is None
