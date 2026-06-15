"""InMemoryMailbox — default backend, zero external dependencies."""

from __future__ import annotations

import asyncio
import json
import threading
import time as _time
from typing import Any

from openagents_orchestration.mailbox.base import Mailbox
from openagents_orchestration.models.message import MAX_PAYLOAD_BYTES, StructuredMessage


class InMemoryMailbox(Mailbox):
    """Per-agent priority queue using a sorted list.

    Differences from the legacy ``_pending_messages`` list:
    - Isolated per-agent queue (no global list)
    - Priority ordering
    - Back-pressure via max_size
    - Ack / nack with re-delivery
    - Dead-letter queue (DLQ)
    - Idempotency-key deduplication
    - Payload size validation
    - asyncio.Lock for concurrency safety
    """

    def __init__(
        self,
        max_size: int = 1000,
        *,
        rate_limit_per_s: float = 0,
        burst_size: int = 0,
        circuit_threshold: int = 10,
        circuit_cooldown_s: float = 30.0,
        nack_backoff_base_s: float = 1.0,
    ) -> None:
        self._max_size = max_size
        self._buffer: list[tuple[int, int, StructuredMessage]] = []  # (priority, seq, msg)
        self._seq = 0
        self._in_flight: dict[str, tuple[int, int, StructuredMessage]] = {}
        self._acked: set[str] = set()
        self._nack_counts: dict[str, int] = {}
        self._nack_retry_at: dict[str, float] = {}  # msg_id → earliest retry timestamp
        self._dlq: list[dict[str, Any]] = []
        self._max_dlq = 100
        self._idempotency_keys: set[str] = set()
        self._idempotency_keys_order: list[str] = []  # FIFO eviction order
        self._max_idempotency_keys = 10_000
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()  # guards sync wrapper access from non-async callers
        # Lazy-import MailboxMetrics to avoid circular import with observability
        from openagents_orchestration.observability.mailbox_metrics import (
            MailboxMetrics as _MM,
        )
        self.metrics = _MM()
        # Token bucket rate limiter (0 = disabled)
        self._rate_limit_per_s = rate_limit_per_s
        self._burst_size = burst_size if burst_size > 0 else max(int(rate_limit_per_s), 1)
        self._tokens = float(self._burst_size)
        self._last_refill = _time.monotonic()
        # Circuit breaker (0 = disabled)
        self._circuit_threshold = circuit_threshold
        self._circuit_cooldown_s = circuit_cooldown_s
        self._circuit_failures: int = 0
        self._circuit_open_at: float = 0.0
        self._circuit_state: str = "closed"  # closed | open | half_open
        # Nack backoff
        self._nack_backoff_base_s = nack_backoff_base_s

    # -- internal helpers ----------------------------------------------------

    def _insert_sorted(self, item: tuple[int, int, StructuredMessage]) -> None:
        """Insert item keeping buffer sorted by (priority, seq)."""
        # Binary search for insertion point
        lo, hi = 0, len(self._buffer)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._buffer[mid][0:2] < item[0:2]:
                lo = mid + 1
            else:
                hi = mid
        self._buffer.insert(lo, item)

    def _validate_payload(self, msg: StructuredMessage) -> bool:
        """Return False if the message payload + text exceeds MAX_PAYLOAD_BYTES."""
        try:
            size = len(json.dumps(msg.payload, default=str)) + len(msg.text.encode("utf-8"))
            return size <= MAX_PAYLOAD_BYTES
        except Exception:
            return False

    def _check_dedup(self, msg: StructuredMessage) -> bool:
        """Return True if this idempotency_key has already been seen (dedup hit)."""
        ikey = msg.header.idempotency_key
        if ikey is None:
            return False
        if ikey in self._idempotency_keys:
            return True
        # FIFO eviction: remove oldest keys to stay under the limit.
        # This preserves recent keys rather than clearing everything.
        while len(self._idempotency_keys) >= self._max_idempotency_keys:
            if self._idempotency_keys_order:
                oldest = self._idempotency_keys_order.pop(0)
                self._idempotency_keys.discard(oldest)
            else:
                break  # safety: shouldn't happen, but prevents infinite loop
        self._idempotency_keys.add(ikey)
        self._idempotency_keys_order.append(ikey)
        return False

    # -- Mailbox API ---------------------------------------------------------

    def _refill_tokens(self) -> None:
        """Refill token bucket based on elapsed time. Call under lock."""
        if self._rate_limit_per_s <= 0:
            return
        now = _time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst_size, self._tokens + elapsed * self._rate_limit_per_s)
        self._last_refill = now

    def _consume_rate_token(self) -> bool:
        """Try to consume a rate-limit token.  Returns True if allowed."""
        if self._rate_limit_per_s <= 0:
            return True
        self._refill_tokens()
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    def _check_circuit(self) -> bool:
        """Return True if the circuit is open (enqueue blocked)."""
        if self._circuit_threshold <= 0:
            return False
        if self._circuit_state == "closed":
            return False
        now = _time.monotonic()
        if self._circuit_state == "open":
            if now - self._circuit_open_at >= self._circuit_cooldown_s:
                self._circuit_state = "half_open"
                return False
            return True
        # half_open: allow one attempt; if it fails, re-open
        return False

    def _record_enqueue_failure(self) -> None:
        """Called when an enqueue is rejected.  May trip the circuit."""
        if self._circuit_threshold <= 0:
            return
        self._circuit_failures += 1
        if self._circuit_failures >= self._circuit_threshold and self._circuit_state == "closed":
            self._circuit_state = "open"
            self._circuit_open_at = _time.monotonic()

    def _record_enqueue_success(self) -> None:
        """Reset circuit on success (in half_open → closed)."""
        if self._circuit_state == "half_open":
            self._circuit_state = "closed"
        self._circuit_failures = 0

    async def enqueue(self, msg: StructuredMessage) -> bool:
        async with self._lock:
            if msg.is_expired:
                self.metrics.expired += 1
                return True  # Silently drop expired messages
            if self._check_dedup(msg):
                self.metrics.enqueued += 1  # Idempotent success
                self._record_enqueue_success()
                return True  # Idempotent: already delivered
            if not self._validate_payload(msg):
                self.metrics.enqueue_rejected += 1
                self._record_enqueue_failure()
                return False  # Payload too large
            if not self._consume_rate_token():
                self.metrics.enqueue_rejected += 1
                self._record_enqueue_failure()
                return False  # Rate limit exceeded
            if self._check_circuit():
                self.metrics.enqueue_rejected += 1
                return False  # Circuit open
            if len(self._buffer) >= self._max_size:
                self.metrics.enqueue_rejected += 1
                self._record_enqueue_failure()
                return False
            self._seq += 1
            self._insert_sorted((msg.priority, self._seq, msg))
            self.metrics.enqueued += 1
            self._record_enqueue_success()
            return True

    async def dequeue_specific(self, msg_id: str) -> StructuredMessage | None:
        """Pop and return a specific message by msg_id.  Returns None if not found.

        Used by the collaboration loop for targeted signal claim — peek finds
        the signal, then dequeue_specific pops it without touching other messages
        in the queue.
        """
        async with self._lock:
            for i, (_priority, _seq, msg) in enumerate(self._buffer):
                if msg.msg_id == msg_id:
                    if msg.msg_id in self._acked or msg.msg_id in self._in_flight:
                        return None
                    self._in_flight[msg_id] = self._buffer.pop(i)
                    msg.header = msg.header.with_delivery()
                    self.metrics.dequeued += 1
                    return msg
        return None

    async def dequeue(self, batch_size: int = 10) -> list[StructuredMessage]:
        async with self._lock:
            results: list[StructuredMessage] = []
            now = _time.monotonic()
            i = 0
            while i < len(self._buffer) and len(results) < batch_size:
                _priority, _seq, msg = self._buffer[i]
                if msg.msg_id in self._acked or msg.msg_id in self._in_flight:
                    i += 1
                    continue
                # Honor nack retry backoff
                retry_at = self._nack_retry_at.get(msg.msg_id)
                if retry_at is not None and now < retry_at:
                    i += 1
                    continue
                if msg.is_expired:
                    del self._buffer[i]
                    continue
                self._in_flight[msg.msg_id] = self._buffer.pop(i)
                msg.header = msg.header.with_delivery()
                results.append(msg)
                self.metrics.dequeued += 1
            return results

    async def peek(
        self,
        limit: int = 5,
        *,
        msg_type: str | None = None,
        sender: str | None = None,
        priority_min: int | None = None,
    ) -> list[StructuredMessage]:
        """Inspect messages without removing them.  Supports optional filters."""
        async with self._lock:
            results: list[StructuredMessage] = []
            for _priority, _seq, msg in self._buffer:
                if msg.msg_id in self._acked or msg.msg_id in self._in_flight:
                    continue
                # Honor nack retry backoff
                retry_at = self._nack_retry_at.get(msg.msg_id)
                if retry_at is not None and _time.monotonic() < retry_at:
                    continue
                if msg.is_expired:
                    continue
                # Apply filters
                if msg_type is not None and msg.msg_type.value != msg_type:
                    continue
                if sender is not None and msg.sender != sender:
                    continue
                if priority_min is not None and msg.header.priority.value > priority_min:
                    continue
                results.append(msg)
                if len(results) >= limit:
                    break
            return results

    async def ack(self, msg_id: str) -> None:
        async with self._lock:
            self._acked.add(msg_id)
            self._in_flight.pop(msg_id, None)
            self._nack_counts.pop(msg_id, None)
            self._nack_retry_at.pop(msg_id, None)
            self.metrics.acked += 1

    async def nack(self, msg_id: str, reason: str = "") -> None:
        async with self._lock:
            count = self._nack_counts.get(msg_id, 0) + 1
            if count >= 3:
                # Move to DLQ — store full message data for replay
                self._acked.add(msg_id)
                item = self._in_flight.pop(msg_id, None)
                self._nack_counts.pop(msg_id, None)
                self._nack_retry_at.pop(msg_id, None)
                dlq_entry = {"msg_id": msg_id, "reason": reason, "nack_count": count}
                if item is not None:
                    # Bump priority so replayed messages get higher urgency
                    msg_data = item[2].to_dict()
                    old_pri = msg_data.get("priority", 2)
                    if old_pri > 0:
                        msg_data["priority"] = old_pri - 1
                    dlq_entry["data"] = msg_data
                self._dlq.append(dlq_entry)
                if len(self._dlq) > self._max_dlq:
                    self._dlq.pop(0)
                self.metrics.dlq_moved += 1
            else:
                # Re-queue for retry with exponential backoff
                self._nack_counts[msg_id] = count
                # Backoff: base, base*2, base*4 for nack #1, #2, #3
                backoff_s = self._nack_backoff_base_s * (2 ** (count - 1))
                self._nack_retry_at[msg_id] = _time.monotonic() + backoff_s
                item = self._in_flight.pop(msg_id, None)
                if item is not None:
                    self._insert_sorted(item)
                self.metrics.nacked += 1

    async def size(self) -> int:
        """Current queue depth (async for ABC compatibility)."""
        async with self._lock:
            return len(self._buffer)

    async def clear_expired(self) -> int:
        """Remove expired messages from buffer.  Returns count removed."""
        async with self._lock:
            before = len(self._buffer)
            self._buffer = [
                item for item in self._buffer
                if not item[2].is_expired
            ]
            return before - len(self._buffer)

    # -- DLQ hooks -----------------------------------------------------------

    async def dlq_size(self) -> int:
        async with self._lock:
            return len(self._dlq)

    async def dlq_peek(self, limit: int = 5) -> list[dict[str, Any]]:
        async with self._lock:
            return self._dlq[-limit:]

    async def dlq_replay(self, msg_id: str) -> bool:
        """Re-enqueue a dead-letter message for re-processing.

        Looks up the message in the DLQ, reconstructs the StructuredMessage
        from stored data, and inserts it back into the main queue.
        """
        async with self._lock:
            for i, entry in enumerate(self._dlq):
                if entry.get("msg_id") == msg_id:
                    data = entry.get("data")
                    if data is None:
                        return False
                    msg = StructuredMessage.from_dict(data)
                    # Remove from DLQ
                    self._dlq.pop(i)
                    # Clear ack and nack state so the message is fresh
                    self._acked.discard(msg_id)
                    self._nack_counts.pop(msg_id, None)
                    self._nack_retry_at.pop(msg_id, None)
                    # Re-insert as high priority (the Director explicitly replayed it)
                    self._seq += 1
                    self._insert_sorted((msg.header.priority.value, self._seq, msg))
                    self.metrics.dlq_replayed += 1
                    return True
            return False

    # -- synchronous compatibility helpers -----------------------------------
    # The legacy StateBoard mail API is synchronous.  InMemoryMailbox has no
    # real I/O, so we expose sync wrappers that operate directly on the
    # internal buffers under threading.Lock.  These are intended for
    # StateBoard internal use only.

    def _sync_enqueue(self, msg: StructuredMessage) -> bool:
        """Synchronous version of enqueue().  Thread-safe via _sync_lock."""
        if msg.is_expired:
            self.metrics.expired += 1
            return True
        with self._sync_lock:
            if self._check_dedup(msg):
                self.metrics.enqueued += 1
                return True
            if not self._validate_payload(msg):
                self.metrics.enqueue_rejected += 1
                return False
            if not self._consume_rate_token():
                self.metrics.enqueue_rejected += 1
                return False  # Rate limit exceeded
            if len(self._buffer) >= self._max_size:
                self.metrics.enqueue_rejected += 1
                return False
            self._seq += 1
            self._insert_sorted((msg.priority, self._seq, msg))
            self.metrics.enqueued += 1
        return True

    def _sync_peek(self, limit: int = 5) -> list[StructuredMessage]:
        """Synchronous version of peek().  Thread-safe via _sync_lock."""
        results: list[StructuredMessage] = []
        now = _time.monotonic()
        with self._sync_lock:
            for _priority, _seq, msg in self._buffer:
                if msg.msg_id in self._acked or msg.msg_id in self._in_flight:
                    continue
                # Honor nack retry backoff (parity with async peek)
                retry_at = self._nack_retry_at.get(msg.msg_id)
                if retry_at is not None and now < retry_at:
                    continue
                if msg.is_expired:
                    continue
                results.append(msg)
                if len(results) >= limit:
                    break
        return results

    def _sync_clear(self) -> int:
        """Clear all pending messages.  Thread-safe via _sync_lock.
        Returns number removed."""
        with self._sync_lock:
            before = len(self._buffer)
            self._buffer = []
            self._in_flight.clear()
            self._idempotency_keys.clear()
            self._idempotency_keys_order.clear()
            self._nack_retry_at.clear()
            # Reset circuit breaker
            self._circuit_failures = 0
            self._circuit_state = "closed"
            return before
