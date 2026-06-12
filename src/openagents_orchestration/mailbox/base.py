"""Mailbox ABC — per-agent message queue interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from openagents_orchestration.models.message import StructuredMessage


class Mailbox(ABC):
    """An isolated message queue for a single agent.

    Guarantees:
    - Messages are returned ordered by priority (lower value = higher priority).
    - Expired messages are silently dropped on dequeue.
    - enqueue may return False to signal back-pressure.
    """

    @abstractmethod
    async def enqueue(self, msg: StructuredMessage) -> bool:
        """Deliver a message.  Returns False when the queue is full."""

    @abstractmethod
    async def dequeue(self, batch_size: int = 10) -> list[StructuredMessage]:
        """Pull messages (ordered by priority).  Does NOT acknowledge."""

    @abstractmethod
    async def dequeue_specific(self, msg_id: str) -> StructuredMessage | None:
        """Pop and return a specific message by msg_id, or None if not found."""

    @abstractmethod
    async def peek(
        self,
        limit: int = 5,
        *,
        msg_type: str | None = None,
        sender: str | None = None,
        priority_min: int | None = None,
    ) -> list[StructuredMessage]:
        """Inspect messages without removing them.

        Optional filters:
        - ``msg_type``: only messages of this type (e.g. "signal", "command").
        - ``sender``: only messages from this sender.
        - ``priority_min``: only messages with priority <= this value (lower = higher).
        """

    @abstractmethod
    async def ack(self, msg_id: str) -> None:
        """Acknowledge that a message has been fully processed."""

    @abstractmethod
    async def nack(self, msg_id: str, reason: str = "") -> None:
        """Negative-acknowledge — message is re-queued or moved to DLQ."""

    @abstractmethod
    async def size(self) -> int:
        """Current queue depth."""

    @abstractmethod
    async def clear_expired(self) -> int:
        """Purge expired messages.  Returns number removed."""

    # -- optional DLQ hooks (backends that support it) -----------------------

    async def dlq_size(self) -> int:
        """Number of dead-letter messages.  Default: 0."""
        return 0

    async def dlq_peek(self, limit: int = 5) -> list[dict[str, Any]]:
        """Inspect dead-letter messages.  Default: []."""
        return []

    async def dlq_replay(self, msg_id: str) -> bool:
        """Re-enqueue a dead-letter message for re-processing.  Default: no-op.

        Returns True if the message was found and replayed.
        """
        return False
