"""MailboxMetrics — per-mailbox observability counters.

Tracks enqueue/dequeue/ack/nack/DLQ events and latency for monitoring.
Integrated into InMemoryMailbox; wire into OrchestrationMetrics for
Prometheus export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MailboxMetrics:
    """Per-mailbox counters and latency histogram.

    All counters are monotonic (only increment).  Latency is tracked as
    a simple sum/count pair for average computation.
    """

    enqueued: int = 0
    enqueue_rejected: int = 0  # back-pressure, rate-limit, or oversized
    dequeued: int = 0
    acked: int = 0
    nacked: int = 0
    expired: int = 0
    dlq_moved: int = 0
    dlq_replayed: int = 0

    # Latency tracking (nanoseconds)
    _enqueue_latency_ns_sum: int = 0
    _enqueue_latency_ns_count: int = 0

    @property
    def avg_enqueue_latency_us(self) -> float:
        """Average enqueue latency in microseconds, or 0.0 if no data."""
        if self._enqueue_latency_ns_count == 0:
            return 0.0
        return (self._enqueue_latency_ns_sum / self._enqueue_latency_ns_count) / 1000.0

    def record_enqueue_latency_ns(self, ns: int) -> None:
        self._enqueue_latency_ns_sum += ns
        self._enqueue_latency_ns_count += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a dict suitable for logging or Prometheus export."""
        return {
            "enqueued": self.enqueued,
            "enqueue_rejected": self.enqueue_rejected,
            "dequeued": self.dequeued,
            "acked": self.acked,
            "nacked": self.nacked,
            "expired": self.expired,
            "dlq_moved": self.dlq_moved,
            "dlq_replayed": self.dlq_replayed,
            "avg_enqueue_latency_us": round(self.avg_enqueue_latency_us, 1),
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.enqueued = 0
        self.enqueue_rejected = 0
        self.dequeued = 0
        self.acked = 0
        self.nacked = 0
        self.expired = 0
        self.dlq_moved = 0
        self.dlq_replayed = 0
        self._enqueue_latency_ns_sum = 0
        self._enqueue_latency_ns_count = 0
