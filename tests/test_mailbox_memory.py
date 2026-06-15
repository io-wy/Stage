"""Tests for InMemoryMailbox."""

from __future__ import annotations

import asyncio
from datetime import UTC

import pytest

from openagents_orchestration.mailbox.memory import InMemoryMailbox
from openagents_orchestration.models.message import (
    MAX_PAYLOAD_BYTES,
    MessageHeader,
    Priority,
    StructuredMessage,
)


@pytest.fixture
def mailbox():
    return InMemoryMailbox(max_size=10, nack_backoff_base_s=0)


class TestInMemoryMailbox:
    async def test_enqueue_and_dequeue(self, mailbox):
        msg = StructuredMessage.from_text("a", "b", "hello")
        ok = await mailbox.enqueue(msg)
        assert ok is True

        msgs = await mailbox.dequeue(batch_size=5)
        assert len(msgs) == 1
        assert msgs[0].text == "hello"

    async def test_dequeue_specific(self, mailbox):
        """dequeue_specific pops an exact message by msg_id without touching others."""
        first = StructuredMessage.from_text("a", "b", "first")
        second = StructuredMessage.from_text("a", "b", "second")
        await mailbox.enqueue(first)
        await mailbox.enqueue(second)

        claimed = await mailbox.dequeue_specific(second.msg_id)
        assert claimed is not None
        assert claimed.msg_id == second.msg_id
        assert claimed.text == "second"

        # First message should still be claimable
        remaining = await mailbox.dequeue(batch_size=10)
        assert len(remaining) == 1
        assert remaining[0].text == "first"

    async def test_dequeue_specific_not_found(self, mailbox):
        assert await mailbox.dequeue_specific("nonexistent") is None

    async def test_priority_ordering(self, mailbox):
        low = StructuredMessage(
            header=MessageHeader(sender="a", recipient="b", priority=Priority.LOW),
            text="low",
        )
        high = StructuredMessage(
            header=MessageHeader(sender="a", recipient="b", priority=Priority.HIGH),
            text="high",
        )
        normal = StructuredMessage(
            header=MessageHeader(sender="a", recipient="b", priority=Priority.NORMAL),
            text="normal",
        )
        await mailbox.enqueue(low)
        await mailbox.enqueue(high)
        await mailbox.enqueue(normal)

        msgs = await mailbox.dequeue(batch_size=3)
        texts = [m.text for m in msgs]
        assert texts == ["high", "normal", "low"]

    async def test_back_pressure(self):
        mbox = InMemoryMailbox(max_size=2)
        msg = StructuredMessage.from_text("a", "b", "x")
        assert await mbox.enqueue(msg) is True
        assert await mbox.enqueue(msg) is True
        # Third enqueue should fail (queue full)
        assert await mbox.enqueue(msg) is False

    async def test_ack_prevents_redelivery(self, mailbox):
        msg = StructuredMessage.from_text("a", "b", "hello")
        await mailbox.enqueue(msg)

        msgs = await mailbox.dequeue(batch_size=1)
        assert len(msgs) == 1

        await mailbox.ack(msg.msg_id)

        # Second dequeue should be empty
        msgs2 = await mailbox.dequeue(batch_size=1)
        assert len(msgs2) == 0

    async def test_nack_moves_to_dlq(self, mailbox):
        msg = StructuredMessage.from_text("a", "b", "fail_me")
        await mailbox.enqueue(msg)

        msgs = await mailbox.dequeue(batch_size=1)
        assert len(msgs) == 1

        # First nack — stays in pending for retry
        await mailbox.nack(msg.msg_id, "temp error")
        assert await mailbox.dlq_size() == 0

        msgs2 = await mailbox.dequeue(batch_size=1)
        assert len(msgs2) == 1  # Re-delivered

        # Second nack
        await mailbox.nack(msg.msg_id, "temp error")
        msgs3 = await mailbox.dequeue(batch_size=1)
        assert len(msgs3) == 1  # Third delivery

        # Third nack — moves to DLQ
        await mailbox.nack(msg.msg_id, "permanent error")
        assert await mailbox.dlq_size() == 1

        msgs4 = await mailbox.dequeue(batch_size=1)
        assert len(msgs4) == 0  # No more redelivery

    async def test_peek_does_not_consume(self, mailbox):
        msg = StructuredMessage.from_text("a", "b", "peek")
        await mailbox.enqueue(msg)

        peeked = await mailbox.peek(limit=1)
        assert len(peeked) == 1

        # Still available for dequeue
        msgs = await mailbox.dequeue(batch_size=1)
        assert len(msgs) == 1

    async def test_expired_messages_dropped(self, mailbox):
        from datetime import datetime, timedelta

        expired = StructuredMessage(
            header=MessageHeader(
                sender="a",
                recipient="b",
                created_at=datetime.now(UTC) - timedelta(seconds=10),
                ttl_s=1.0,
            ),
            text="old",
        )
        fresh = StructuredMessage.from_text("a", "b", "fresh")

        await mailbox.enqueue(expired)
        await mailbox.enqueue(fresh)

        msgs = await mailbox.dequeue(batch_size=2)
        assert len(msgs) == 1
        assert msgs[0].text == "fresh"

    async def test_clear_expired(self, mailbox):
        from datetime import datetime, timedelta

        # Expired messages are dropped at enqueue time, so nothing to clear
        expired = StructuredMessage(
            header=MessageHeader(
                sender="a",
                recipient="b",
                created_at=datetime.now(UTC) - timedelta(seconds=10),
                ttl_s=1.0,
            ),
            text="old",
        )
        await mailbox.enqueue(expired)
        # Already dropped at enqueue
        assert await mailbox.size() == 0

        # Create a message that will expire after enqueue
        soon_expired = StructuredMessage(
            header=MessageHeader(
                sender="a",
                recipient="b",
                ttl_s=0.05,  # 50ms TTL
            ),
            text="soon",
        )
        await mailbox.enqueue(soon_expired)
        assert await mailbox.size() == 1

        import asyncio
        await asyncio.sleep(0.1)

        removed = await mailbox.clear_expired()
        assert removed == 1
        assert await mailbox.size() == 0

    async def test_size_tracking(self, mailbox):
        assert await mailbox.size() == 0
        await mailbox.enqueue(StructuredMessage.from_text("a", "b", "x"))
        assert await mailbox.size() == 1
        await mailbox.dequeue(batch_size=1)
        assert await mailbox.size() == 0

    async def test_idempotency_key_dedup(self, mailbox):
        """Messages with the same idempotency_key are only enqueued once."""
        hdr = MessageHeader(idempotency_key="ikey-001")
        msg1 = StructuredMessage(header=hdr, text="first")
        msg2 = StructuredMessage(header=hdr, text="second")  # same key

        assert await mailbox.enqueue(msg1) is True
        assert await mailbox.size() == 1
        # Second enqueue with same key returns True (idempotent) but is dropped
        assert await mailbox.enqueue(msg2) is True
        assert await mailbox.size() == 1  # still 1

        msgs = await mailbox.dequeue()
        assert len(msgs) == 1
        assert msgs[0].text == "first"

    async def test_idempotency_key_null_not_deduped(self, mailbox):
        """Messages without idempotency_key are never deduped."""
        msg1 = StructuredMessage.from_text("a", "b", "hello")
        msg2 = StructuredMessage.from_text("a", "b", "hello")
        assert await mailbox.enqueue(msg1) is True
        assert await mailbox.enqueue(msg2) is True
        assert await mailbox.size() == 2

    async def test_sync_enqueue_respects_dedup(self, mailbox):
        """_sync_enqueue also checks idempotency."""
        hdr = MessageHeader(idempotency_key="sync-dedup")
        msg1 = StructuredMessage(header=hdr, text="one")
        msg2 = StructuredMessage(header=hdr, text="two")

        assert mailbox._sync_enqueue(msg1) is True
        assert mailbox._sync_enqueue(msg2) is True  # deduped
        assert await mailbox.size() == 1

    async def test_payload_size_rejected(self, mailbox):
        """Oversized payloads are rejected."""
        big_data = "x" * (MAX_PAYLOAD_BYTES + 1)  # exceeds 1MB
        msg = StructuredMessage.from_text("a", "b", big_data)
        assert await mailbox.enqueue(msg) is False
        assert await mailbox.size() == 0

    async def test_payload_size_accepted_when_within_limit(self, mailbox):
        """Normal-sized payloads pass validation."""
        msg = StructuredMessage.from_text("a", "b", "normal message")
        assert await mailbox.enqueue(msg) is True
        assert await mailbox.size() == 1

    async def test_clear_resets_idempotency_keys(self, mailbox):
        """Clearing mailbox also resets the idempotency set."""
        hdr = MessageHeader(idempotency_key="clear-test")
        msg1 = StructuredMessage(header=hdr, text="first")
        msg2 = StructuredMessage(header=hdr, text="second")

        await mailbox.enqueue(msg1)
        assert await mailbox.size() == 1
        mailbox._sync_clear()
        assert await mailbox.size() == 0

        # After clear, same key should be enqueued again
        assert await mailbox.enqueue(msg2) is True
        assert await mailbox.size() == 1

    async def test_dlq_replay_re_enqueues(self, mailbox):
        """Replaying a DLQ message puts it back in the main queue."""
        msg = StructuredMessage.from_text("a", "b", "retry me")
        await mailbox.enqueue(msg)

        # Nack 3 times to move to DLQ
        for i in range(3):
            msgs = await mailbox.dequeue(batch_size=1)
            if msgs:
                await mailbox.nack(msgs[0].msg_id, f"fail {i}")

        assert await mailbox.dlq_size() == 1
        assert await mailbox.size() == 0

        # Replay
        ok = await mailbox.dlq_replay(msg.msg_id)
        assert ok is True
        assert await mailbox.dlq_size() == 0

        replayed = await mailbox.dequeue()
        assert len(replayed) == 1
        assert replayed[0].text == "retry me"

    async def test_dlq_replay_unknown_msg(self, mailbox):
        """dlq_replay returns False for unknown msg_ids."""
        ok = await mailbox.dlq_replay("nonexistent")
        assert ok is False

    async def test_dlq_replay_data_loss_missing_msg(self, mailbox):
        """dlq_replay returns False when DLQ entry has no message data."""
        msg = StructuredMessage.from_text("a", "b", "ghost")
        await mailbox.enqueue(msg)
        msgs = await mailbox.dequeue(batch_size=1)
        assert len(msgs) == 1

        # Corrupt the DLQ entry by simulating nack but removing data
        await mailbox.nack(msgs[0].msg_id, "reason")
        await mailbox.nack(msgs[0].msg_id, "reason")
        await mailbox.nack(msgs[0].msg_id, "reason")
        # Manually remove data to simulate legacy DLQ entry
        mailbox._dlq[-1]["data"] = None

        ok = await mailbox.dlq_replay(msg.msg_id)
        assert ok is False
        # Entry should still be in DLQ
        assert await mailbox.dlq_size() == 1

    async def test_peek_filter_by_msg_type(self):
        """peek() supports filtering by msg_type."""
        mbox = InMemoryMailbox(max_size=10)
        sig = StructuredMessage.signal("a", "b", "review_ready", "t1", text="ready")
        cmd = StructuredMessage.command("c", "d", "implement")
        await mbox.enqueue(sig)
        await mbox.enqueue(cmd)

        signals = await mbox.peek(limit=5, msg_type="signal")
        assert len(signals) == 1
        assert signals[0].msg_type.value == "signal"

        commands = await mbox.peek(limit=5, msg_type="command")
        assert len(commands) == 1

    async def test_priority_bump_on_dlq_nack(self):
        """Messages that reach DLQ get their priority bumped by 1 level."""
        mbox = InMemoryMailbox(max_size=10, nack_backoff_base_s=0)
        msg = StructuredMessage.from_text("a", "b", "low_prio")
        # Override to LOW priority
        from openagents_orchestration.models.message import MessageHeader, Priority
        msg.header = MessageHeader(
            msg_id=msg.header.msg_id, sender="a", recipient="b",
            priority=Priority.LOW,
        )
        await mbox.enqueue(msg)

        for _ in range(3):
            claimed = await mbox.dequeue(batch_size=1)
            if claimed:
                await mbox.nack(claimed[0].msg_id, "fail")

        dlq_items = await mbox.dlq_peek(limit=1)
        assert len(dlq_items) == 1
        data = dlq_items[0]["data"]
        assert data["priority"] == Priority.NORMAL.value  # bumped from LOW→NORMAL

    async def test_peek_filter_by_sender(self):
        """peek() supports filtering by sender."""
        mbox = InMemoryMailbox(max_size=10)
        await mbox.enqueue(StructuredMessage.from_text("a", "b", "from_a"))
        await mbox.enqueue(StructuredMessage.from_text("c", "b", "from_c"))

        a_msgs = await mbox.peek(limit=5, sender="a")
        assert len(a_msgs) == 1
        assert a_msgs[0].text == "from_a"

    async def test_concurrent_enqueue_dequeue(self):
        """Concurrent producers and consumers do not corrupt state."""
        mbox = InMemoryMailbox(max_size=1000)
        N = 100

        async def producer(start: int):
            for i in range(start, start + N):
                msg = StructuredMessage.from_text("a", "b", f"msg-{i}")
                await mbox.enqueue(msg)

        async def consumer(total: int) -> list[str]:
            texts: list[str] = []
            while len(texts) < total:
                msgs = await mbox.dequeue(batch_size=10)
                for m in msgs:
                    texts.append(m.text)
                    await mbox.ack(m.msg_id)
                if not msgs:
                    await asyncio.sleep(0.001)
            return texts

        # 3 concurrent producers
        producers = [producer(i * N) for i in range(3)]
        cons = consumer(3 * N)

        await asyncio.gather(*producers, cons)

        assert await mbox.size() == 0
        # Verify all messages were delivered (consumer handled 3*N)
        pass  # consumer coroutine already verified all texts collected

    async def test_rate_limit_rejects_above_burst(self):
        """Rate-limited mailbox rejects enqueues beyond burst size."""
        mbox = InMemoryMailbox(max_size=100, rate_limit_per_s=10, burst_size=3)

        # First burst_size messages should succeed
        for i in range(3):
            ok = await mbox.enqueue(StructuredMessage.from_text("a", "b", f"msg-{i}"))
            assert ok is True

        # 4th should be rejected (rate limit)
        ok = await mbox.enqueue(StructuredMessage.from_text("a", "b", "blocked"))
        assert ok is False

    async def test_rate_limit_disabled_by_default(self):
        """When rate_limit_per_s=0, no rate limiting is applied."""
        mbox = InMemoryMailbox(max_size=100)
        for i in range(50):
            ok = await mbox.enqueue(StructuredMessage.from_text("a", "b", f"msg-{i}"))
            assert ok is True

    async def test_nack_exponential_backoff(self):
        """Nacked messages are not immediately re-deliverable (exponential backoff)."""
        mbox = InMemoryMailbox(max_size=10, nack_backoff_base_s=0.1)
        msg = StructuredMessage.from_text("a", "b", "backoff")
        await mbox.enqueue(msg)
        msgs = await mbox.dequeue(batch_size=1)
        assert len(msgs) == 1

        await mbox.nack(msgs[0].msg_id, "temp")
        # Message re-queued but not claimable yet (retry_at in future)
        msgs2 = await mbox.dequeue(batch_size=1)
        assert len(msgs2) == 0  # backoff not yet elapsed

        await asyncio.sleep(0.15)  # wait past backoff (0.1s)
        msgs3 = await mbox.dequeue(batch_size=1)
        assert len(msgs3) == 1
        assert msgs3[0].msg_id == msg.msg_id

    async def test_circuit_breaker_opens_on_repeated_failure(self):
        """After consecutive enqueue failures, the circuit opens."""
        mbox = InMemoryMailbox(max_size=2, circuit_threshold=3, circuit_cooldown_s=60)
        msg = StructuredMessage.from_text("a", "b", "fill")

        # Fill the mailbox (max_size=2)
        assert await mbox.enqueue(msg) is True
        assert await mbox.enqueue(msg) is True

        # 3 more enqueues fail → circuit opens
        for _ in range(3):
            ok = await mbox.enqueue(msg)
            assert ok is False

        assert mbox._circuit_state == "open"

        # Enqueues still fail after circuit is open
        ok = await mbox.enqueue(msg)
        assert ok is False

    async def test_circuit_breaker_half_open_after_cooldown(self):
        """Circuit goes half_open after cooldown, then closes on success."""
        mbox = InMemoryMailbox(max_size=2, circuit_threshold=3, circuit_cooldown_s=0.1)
        msg = StructuredMessage.from_text("a", "b", "fill")

        await mbox.enqueue(msg)
        await mbox.enqueue(msg)

        for _ in range(3):
            await mbox.enqueue(msg)  # fail, trips circuit

        assert mbox._circuit_state == "open"

        # Wait for cooldown
        await asyncio.sleep(0.15)

        # Now dequeue to make space, then enqueue should succeed (half_open → closed)
        await mbox.dequeue(batch_size=1)

        ok = await mbox.enqueue(msg)
        assert ok is True
        assert mbox._circuit_state == "closed"

    async def test_concurrent_ack_nack_replay(self):
        """Concurrent nack→DLQ and concurrent replay works under lock."""
        mbox = InMemoryMailbox(max_size=100, nack_backoff_base_s=0)

        # Enqueue 10 messages
        for i in range(10):
            await mbox.enqueue(StructuredMessage.from_text("a", "b", f"msg-{i}"))

        claimed = await mbox.dequeue(batch_size=10)
        assert len(claimed) == 10

        # Nack each 3x → DLQ (each nack requires re-dequeue because message
        # goes back into the buffer after nack counts 1 and 2)
        for m in claimed:
            for _ in range(3):
                await mbox.nack(m.msg_id, "test")
                if _ < 2:  # after nacks 1-2, re-dequeue for next nack
                    redelivered = await mbox.dequeue(batch_size=1)
                    assert len(redelivered) == 1
                    assert redelivered[0].msg_id == m.msg_id

        assert await mbox.dlq_size() == 10

        # Concurrently replay all DLQ messages
        dlq_entries = await mbox.dlq_peek(limit=20)
        results = await asyncio.gather(*[
            mbox.dlq_replay(e["msg_id"]) for e in dlq_entries
        ])
        assert all(results)
        assert await mbox.dlq_size() == 0

        replayed = await mbox.dequeue(batch_size=20)
        assert len(replayed) == 10
