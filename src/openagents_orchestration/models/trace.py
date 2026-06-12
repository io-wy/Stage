"""Distributed tracing for agent message flows.

A TraceContext follows a single logical operation (e.g. "implement task t1")
across multiple agents and messages.  It is lightweight and serializable so
it can be embedded in messages and event logs.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TraceContext:
    """A lightweight trace/span identifier with optional timing.

    Attributes:
        trace_id: Root trace identifier shared across the entire call chain.
        span_id: This span's identifier.
        parent_span_id: Identifier of the parent span (None for root spans).
        span_name: Human-readable label for this span (e.g. "spawn_agent").
        started_at: Monotonic timestamp when the span was created.
        finished_at: Monotonic timestamp set by ``finish()``.
    """

    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:12]}")
    span_id: str = field(default_factory=lambda: f"span_{uuid.uuid4().hex[:8]}")
    parent_span_id: str | None = None
    span_name: str = ""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    @property
    def duration_s(self) -> float | None:
        """Elapsed seconds, or None if ``finish()`` hasn't been called."""
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def child(self, name: str = "") -> TraceContext:
        """Create a child span while preserving the root trace_id."""
        suffix = f"-{name}" if name else ""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=f"span_{uuid.uuid4().hex[:8]}{suffix}",
            parent_span_id=self.span_id,
            span_name=name,
        )

    def finish(self) -> TraceContext:
        """Return a frozen copy with ``finished_at`` set to now."""
        object.__setattr__(self, "finished_at", time.monotonic())
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "span_name": self.span_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceContext:
        return cls(
            trace_id=data.get("trace_id") or f"trace_{uuid.uuid4().hex[:12]}",
            span_id=data.get("span_id") or f"span_{uuid.uuid4().hex[:8]}",
            parent_span_id=data.get("parent_span_id"),
            span_name=data.get("span_name", ""),
            started_at=data.get("started_at", 0.0),
            finished_at=data.get("finished_at"),
        )

    def inject_into_message(self, msg: Any) -> None:
        """Set the message's trace fields by replacing the frozen header."""
        from openagents_orchestration.models.message import MessageHeader

        if not isinstance(msg.header, MessageHeader):
            return
        if msg.header.trace_id:
            return
        msg.header = MessageHeader(
            msg_id=msg.header.msg_id,
            parent_id=msg.header.parent_id,
            trace_id=self.trace_id,
            causality=msg.header.causality,
            idempotency_key=msg.header.idempotency_key,
            sender=msg.header.sender,
            recipient=msg.header.recipient,
            msg_type=msg.header.msg_type,
            priority=msg.header.priority,
            created_at=msg.header.created_at,
            ttl_s=msg.header.ttl_s,
            delivery_count=msg.header.delivery_count,
            parent_span_id=self.span_id,
        )
