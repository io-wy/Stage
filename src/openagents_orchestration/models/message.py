"""StructuredMessage — typed, traceable, persistent inter-agent messaging.

Replaces the legacy string-only _pending_messages protocol with a structured
format that carries type, priority, TTL, and causality chain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any


class MessageType(StrEnum):
    COMMAND = "command"
    EVENT = "event"
    NOTIFICATION = "notification"
    SIGNAL = "signal"


class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


# Maximum payload size in bytes (1 MiB).  Messages exceeding this are
# rejected at enqueue time to prevent OOM from oversized payloads.
MAX_PAYLOAD_BYTES: int = 1_048_576


@dataclass(frozen=True, slots=True)
class MessageHeader:
    """Immutable message metadata."""

    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: str | None = None
    trace_id: str = ""
    parent_span_id: str | None = None
    causality: tuple[str, ...] = ()
    idempotency_key: str | None = None

    sender: str = ""
    recipient: str = ""
    msg_type: MessageType = MessageType.NOTIFICATION
    priority: Priority = Priority.NORMAL

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_s: float | None = None

    delivery_count: int = 0

    def with_delivery(self) -> MessageHeader:
        """Return a copy with incremented delivery_count."""
        return MessageHeader(
            msg_id=self.msg_id,
            parent_id=self.parent_id,
            trace_id=self.trace_id,
            parent_span_id=self.parent_span_id,
            causality=self.causality,
            idempotency_key=self.idempotency_key,
            sender=self.sender,
            recipient=self.recipient,
            msg_type=self.msg_type,
            priority=self.priority,
            created_at=self.created_at,
            ttl_s=self.ttl_s,
            delivery_count=self.delivery_count + 1,
        )

    @property
    def is_expired(self) -> bool:
        if self.ttl_s is None:
            return False
        elapsed = (datetime.now(UTC) - self.created_at).total_seconds()
        return elapsed > self.ttl_s


@dataclass(slots=True)
class StructuredMessage:
    """A typed, traceable message exchanged between agents."""

    header: MessageHeader = field(default_factory=MessageHeader)
    payload: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    # -- factories -----------------------------------------------------------

    @classmethod
    def command(
        cls,
        sender: str,
        recipient: str,
        action: str,
        *,
        priority: Priority = Priority.HIGH,
        parent_id: str | None = None,
        trace_id: str = "",
        **params: Any,
    ) -> StructuredMessage:
        return cls(
            header=MessageHeader(
                sender=sender,
                recipient=recipient,
                msg_type=MessageType.COMMAND,
                priority=priority,
                parent_id=parent_id,
                trace_id=trace_id,
            ),
            payload={"action": action, "params": params},
            text=f"[{action}] {params}",
        )

    @classmethod
    def signal(
        cls,
        sender: str,
        recipient: str,
        signal_type: str,
        task_id: str,
        *,
        trace_id: str = "",
        text: str = "",
        **payload: Any,
    ) -> StructuredMessage:
        return cls(
            header=MessageHeader(
                sender=sender,
                recipient=recipient,
                msg_type=MessageType.SIGNAL,
                priority=Priority.HIGH,
                trace_id=trace_id,
            ),
            payload={"signal": signal_type, "task_id": task_id, **payload},
            text=text or f"SIGNAL[{signal_type}] task={task_id}",
        )

    @classmethod
    def from_text(
        cls,
        sender: str,
        recipient: str,
        text: str,
        *,
        msg_type: MessageType = MessageType.NOTIFICATION,
        priority: Priority = Priority.NORMAL,
    ) -> StructuredMessage:
        """Back-compat wrapper: create a message from a plain text string."""
        return cls(
            header=MessageHeader(
                sender=sender,
                recipient=recipient,
                msg_type=msg_type,
                priority=priority,
            ),
            text=text,
        )

    @classmethod
    def event(
        cls,
        sender: str,
        event_type: str,
        *,
        trace_id: str = "",
        **payload: Any,
    ) -> StructuredMessage:
        return cls(
            header=MessageHeader(
                sender=sender,
                recipient="*",
                msg_type=MessageType.EVENT,
                priority=Priority.NORMAL,
                trace_id=trace_id,
            ),
            payload={"event_type": event_type, **payload},
            text=f"EVENT[{event_type}]",
        )

    # -- derived properties --------------------------------------------------

    @property
    def msg_id(self) -> str:
        return self.header.msg_id

    @property
    def sender(self) -> str:
        return self.header.sender

    @property
    def recipient(self) -> str:
        return self.header.recipient

    @property
    def msg_type(self) -> MessageType:
        return self.header.msg_type

    @property
    def is_broadcast(self) -> bool:
        return self.header.recipient == "*"

    @property
    def priority(self) -> int:
        return self.header.priority.value

    @property
    def is_expired(self) -> bool:
        return self.header.is_expired

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.header.msg_id,
            "parent_id": self.header.parent_id,
            "trace_id": self.header.trace_id,
            "parent_span_id": self.header.parent_span_id,
            "causality": list(self.header.causality),
            "idempotency_key": self.header.idempotency_key,
            "sender": self.header.sender,
            "recipient": self.header.recipient,
            "type": self.header.msg_type.value,
            "priority": self.header.priority.value,
            "created_at": self.header.created_at.isoformat(),
            "ttl_s": self.header.ttl_s,
            "delivery_count": self.header.delivery_count,
            "payload": self.payload,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredMessage:
        header = MessageHeader(
            msg_id=data.get("msg_id", ""),
            parent_id=data.get("parent_id"),
            trace_id=data.get("trace_id", ""),
            parent_span_id=data.get("parent_span_id"),
            causality=tuple(data.get("causality", [])),
            idempotency_key=data.get("idempotency_key"),
            sender=data.get("sender", ""),
            recipient=data.get("recipient", ""),
            msg_type=MessageType(data.get("type", "notification")),
            priority=Priority(data.get("priority", 2)),
            created_at=datetime.fromisoformat(data["created_at"]),
            ttl_s=data.get("ttl_s"),
            delivery_count=data.get("delivery_count", 0),
        )
        return cls(
            header=header,
            payload=data.get("payload", {}),
            text=data.get("text", ""),
        )
