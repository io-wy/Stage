"""Adversarial tests for InMemoryMailbox — the default message-bus backend.

Module under test: ``src/openagents_orchestration/mailbox/memory.py`` (420 lines,
zero coverage after the cull — old ``test_mailbox_memory.py`` / ``test_check_dlq.py``
were deleted). The backend advertises priority ordering, back-pressure, ack/nack
re-delivery, a DLQ, idempotency dedup, payload-size limits, and "asyncio.Lock for
concurrency safety". These tests probe each of those claims; ``test_gap_*`` pin
behavior that contradicts the contract or is operationally unsafe.
"""

from __future__ import annotations

import asyncio
import threading

from openagents_orchestration.mailbox.memory import InMemoryMailbox
from openagents_orchestration.models.message import (
    MAX_PAYLOAD_BYTES,
    MessageHeader,
    Priority,
    StructuredMessage,
)


def _msg(text: str = "", *, priority: Priority = Priority.NORMAL) -> StructuredMessage:
    return StructuredMessage.from_text("sender-a", "recipient-b", text, priority=priority)


# ── contract: the parts that work ────────────────────────────────────────────


async def test_priority_ordering_critical_before_low():
    mb = InMemoryMailbox()
    await mb.enqueue(_msg("low", priority=Priority.LOW))
    await mb.enqueue(_msg("crit", priority=Priority.CRITICAL))
    batch = await mb.dequeue(batch_size=2)
    assert [m.text for m in batch] == ["crit", "low"]


async def test_enqueue_silently_drops_already_expired_message():
    """Base contract: expired messages are dropped — here even at enqueue, and
    enqueue still reports success (True), so the sender cannot tell."""
    mb = InMemoryMailbox()
    m = StructuredMessage.signal("a", "b", "sig", "t1", ttl_s=0.0)
    await asyncio.sleep(0.002)
    assert await mb.enqueue(m) is True
    assert await mb.size() == 0


async def test_idempotency_key_dedups_second_enqueue():
    mb = InMemoryMailbox()
    m1 = StructuredMessage(
        header=MessageHeader(sender="a", recipient="b", idempotency_key="k1"),
        text="first",
    )
    m2 = StructuredMessage(
        header=MessageHeader(sender="a", recipient="b", idempotency_key="k1"),
        text="second",
    )
    assert await mb.enqueue(m1) is True
    assert await mb.enqueue(m2) is True  # dedup hit → idempotent success
    assert await mb.size() == 1  # only the first is actually queued


async def test_oversized_payload_rejected():
    mb = InMemoryMailbox()
    m = _msg("")
    m.payload = {"blob": "x" * (MAX_PAYLOAD_BYTES + 100)}
    assert await mb.enqueue(m) is False
    assert await mb.size() == 0


async def test_ack_clears_in_flight():
    mb = InMemoryMailbox()
    await mb.enqueue(_msg("work"))
    (got,) = await mb.dequeue(batch_size=1)
    assert got.msg_id in mb._in_flight
    await mb.ack(got.msg_id)
    assert got.msg_id not in mb._in_flight


async def test_dlq_after_three_nacks_then_replay_roundtrip():
    mb = InMemoryMailbox(nack_backoff_base_s=0.0)
    m = _msg("flaky", priority=Priority.NORMAL)
    await mb.enqueue(m)
    mid = m.msg_id
    for _ in range(3):
        batch = await mb.dequeue()
        assert batch and batch[0].msg_id == mid
        await mb.nack(mid, reason="boom")
    assert await mb.dlq_size() == 1
    assert await mb.size() == 0
    peeked = await mb.dlq_peek()
    assert peeked[0]["msg_id"] == mid and peeked[0]["nack_count"] == 3

    assert await mb.dlq_replay(mid) is True
    assert await mb.dlq_size() == 0
    back = await mb.dequeue()
    assert back and back[0].msg_id == mid


# ── GAP 1: ack() of an unseen id blackholes a future message ──────────────────


async def test_pre_ack_of_unknown_id_does_not_blackhole_future_message():
    """``ack`` now ignores ids that are not currently in-flight, so a premature
    ack cannot permanently block a future message with the same id."""
    mb = InMemoryMailbox()
    await mb.ack("ghost-id")  # ack an id we have never seen
    m = _msg("important")
    m.header = MessageHeader(msg_id="ghost-id", sender="a", recipient="b")
    assert await mb.enqueue(m) is True
    assert await mb.size() == 1
    dequeued = await mb.dequeue()
    assert len(dequeued) == 1
    assert dequeued[0].msg_id == "ghost-id"


async def test_acked_set_is_bounded():
    mb = InMemoryMailbox(max_acked=3)
    # Hand out and ack 5 distinct in-flight messages.
    for i in range(5):
        m = _msg(f"m{i}")
        m.header = MessageHeader(msg_id=f"id{i}", sender="a", recipient="b")
        await mb.enqueue(m)
        dequeued = await mb.dequeue()
        assert len(dequeued) == 1
        await mb.ack(dequeued[0].msg_id)
    assert len(mb._acked) <= 3


async def test_in_flight_message_is_redelivered_after_visibility_timeout():
    """Messages dequeued but not acked/nacked within the visibility timeout are
    automatically re-queued (or DLQ'd after repeated timeouts)."""
    mb = InMemoryMailbox(visibility_timeout_s=0.05)
    await mb.enqueue(_msg("work"))
    first = await mb.dequeue()
    assert len(first) == 1
    # Consumer "crashes" — no ack/nack. Wait for visibility timeout.
    await asyncio.sleep(0.08)
    redelivered = await mb.dequeue()
    assert len(redelivered) == 1
    assert redelivered[0].msg_id == first[0].msg_id



# ── GAP 3: dequeue_specific delivers expired messages ─────────────────────────


async def test_gap_dequeue_specific_delivers_expired_message():
    """The Mailbox ABC promises 'Expired messages are silently dropped on
    dequeue', and ``dequeue``/``peek`` honor it. ``dequeue_specific`` does NOT
    check expiry, so a targeted claim resurrects an expired message."""
    mb = InMemoryMailbox()
    m = StructuredMessage.signal("a", "b", "review_ready", "t1", ttl_s=0.05)
    await mb.enqueue(m)
    await asyncio.sleep(0.08)
    assert (await mb.peek()) == []  # peek correctly hides the expired message
    via_specific = await mb.dequeue_specific(m.msg_id)
    assert via_specific is not None  # GAP: expired message delivered anyway
    assert via_specific.is_expired is True


# ── GAP 4: the synchronous enqueue path bypasses the circuit breaker ──────────


async def test_gap_sync_enqueue_bypasses_circuit_breaker():
    """The async ``enqueue`` checks the circuit breaker; the legacy
    ``_sync_enqueue`` wrapper (used by StateBoard's sync mail API) does not. Once
    the breaker is open, async sends are rejected but sync sends sail straight
    through — back-pressure is not uniformly enforced."""
    mb = InMemoryMailbox(circuit_threshold=2)
    big = {"blob": "x" * (MAX_PAYLOAD_BYTES + 10)}
    for _ in range(2):
        m = _msg("")
        m.payload = big
        assert await mb.enqueue(m) is False  # oversized → failure trips the breaker
    assert mb._circuit_state == "open"
    assert await mb.enqueue(_msg("blocked")) is False  # async path: breaker holds
    assert mb._sync_enqueue(_msg("leaked")) is True  # GAP: sync path ignores breaker


def test_gap_sync_and_async_paths_use_distinct_locks():
    """``enqueue`` guards ``_buffer``/``_seq`` with an ``asyncio.Lock`` while the
    sync wrappers guard the *same* fields with a separate ``threading.Lock``. The
    two locks do not exclude each other, so concurrent sync+async access (an
    SDK-thread tool calling the sync API while the event loop enqueues) races on
    shared state. The 'asyncio.Lock for concurrency safety' claim only covers the
    async path."""
    mb = InMemoryMailbox()
    assert mb._lock is not mb._sync_lock
    assert isinstance(mb._lock, asyncio.Lock)
    assert isinstance(mb._sync_lock, type(threading.Lock()))


# ── GAP 5: acked set grows without bound (memory leak) ────────────────────────


async def test_gap_acked_set_never_shrinks():
    """Every acked id is retained forever — ``ack`` only adds to ``_acked`` and
    nothing ever evicts it (unlike ``_idempotency_keys``, which is FIFO-capped).
    A long-running resident agent's mailbox leaks one set entry per processed
    message."""
    mb = InMemoryMailbox()
    for i in range(50):
        await mb.enqueue(_msg(f"m{i}"))
        (got,) = await mb.dequeue(batch_size=1)
        await mb.ack(got.msg_id)
    assert await mb.size() == 0
    assert len(mb._acked) == 50  # GAP: unbounded, never purged


# ── GAP 6: nack of an unknown id manufactures a phantom DLQ entry ─────────────


async def test_gap_nack_unknown_id_creates_unreplayable_dlq_entry():
    """Nacking an id that was never dequeued still increments its count and, on
    the 3rd nack, pushes a DLQ entry that carries no message ``data`` — so it can
    never be replayed. Bogus ids silently pollute the DLQ."""
    mb = InMemoryMailbox()
    for _ in range(3):
        await mb.nack("never-existed", reason="x")
    assert await mb.dlq_size() == 1  # phantom entry created
    assert await mb.dlq_replay("never-existed") is False  # but has no data to replay
