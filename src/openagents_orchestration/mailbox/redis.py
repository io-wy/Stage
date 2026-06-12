"""RedisMailbox — persistent, distributed mailbox using Redis Streams.

Requires ``redis`` package (``pip install redis``) and a running Redis server.
Gracefully falls back to InMemoryMailbox when Redis is unavailable.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from openagents_orchestration.mailbox.base import Mailbox
from openagents_orchestration.models.message import (
    MAX_PAYLOAD_BYTES,
    StructuredMessage,
)

logger = logging.getLogger(__name__)

# Sentinel to distinguish "redis not installed" from "redis installed but broken"
REDIS_AVAILABLE = False
try:
    import redis.asyncio as aioredis
    from redis.exceptions import ResponseError

    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]
    ResponseError = Exception  # type: ignore[misc,assignment]


class RedisMailbox(Mailbox):
    """Mailbox backed by Redis Streams.

    Data model per agent:
    - Stream ``mailbox:{agent_id}``        — message stream
    - Consumer group ``cg:{agent_id}``     — single consumer group per agent
    - Stream ``dlq:{agent_id}``            — dead-letter queue
    """

    def __init__(
        self,
        redis_client: Any,
        agent_id: str,
        *,
        max_size: int = 10_000,
        max_dlq: int = 1000,
        nack_threshold: int = 3,
        dequeue_block_ms: int = 50,
    ) -> None:
        if not REDIS_AVAILABLE:
            raise RuntimeError("redis package is not installed; run: pip install redis")
        self._redis: Any = redis_client
        self._agent_id = agent_id
        self._stream = f"mailbox:{agent_id}"
        self._dlq_key = f"dlq:{agent_id}"
        self._group = f"cg:{agent_id}"
        self._consumer = f"{agent_id}-{uuid.uuid4().hex[:8]}"
        self._max_size = max_size
        self._max_dlq = max_dlq
        self._nack_threshold = nack_threshold
        self._dequeue_block_ms = dequeue_block_ms
        self._group_created: bool = False

    # -- Mailbox API ---------------------------------------------------------

    async def enqueue(self, msg: StructuredMessage) -> bool:
        if msg.is_expired:
            return True
        # Idempotency-key deduplication via Redis SET
        ikey = msg.header.idempotency_key
        if ikey is not None:
            dedup_key = f"idem:{self._agent_id}:{ikey}"
            # SET NX returns True if the key did not exist
            was_new = await self._redis.set(dedup_key, "1", nx=True, ex=86400)
            if not was_new:
                return True  # Idempotent: already seen
        # Payload size validation
        try:
            payload_size = len(json.dumps(msg.payload, default=str)) + len(msg.text.encode("utf-8"))
            if payload_size > MAX_PAYLOAD_BYTES:
                return False
        except Exception:
            return False
        depth = await self._redis.xlen(self._stream)
        if depth >= self._max_size:
            return False
        await self._redis.xadd(
            self._stream,
            {"data": json.dumps(msg.to_dict(), default=str)},
            maxlen=self._max_size,
            approximate=True,
        )
        return True

    async def dequeue(self, batch_size: int = 10) -> list[StructuredMessage]:
        await self._ensure_group()
        entries = await self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream: ">"},
            count=batch_size,
            block=self._dequeue_block_ms,
        )
        results: list[StructuredMessage] = []
        for _stream_name, msgs in entries:
            for _msg_id, fields in msgs:
                msg = self._decode(fields)
                if msg is not None:
                    msg.header = msg.header.with_delivery()
                    results.append(msg)
        return results

    async def dequeue_specific(self, msg_id: str) -> StructuredMessage | None:
        """Read a specific message by its stream entry id without claiming others."""
        entries = await self._redis.xrange(self._stream, min=msg_id, max=msg_id, count=1)
        if not entries:
            return None
        _entry_id, fields = entries[0]
        msg = self._decode(fields)
        if msg is None:
            return None
        # Use the stream msg_id as the ack target and add to consumer group PEL
        await self._ensure_group()
        await self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream: msg_id},
            count=1,
            block=0,
        )
        msg.header = msg.header.with_delivery()
        return msg

    async def peek(
        self,
        limit: int = 5,
        *,
        msg_type: str | None = None,
        sender: str | None = None,
        priority_min: int | None = None,
    ) -> list[StructuredMessage]:
        entries = await self._redis.xrevrange(self._stream, max="+", min="-", count=limit)
        results: list[StructuredMessage] = []
        for _msg_id, fields in entries:
            msg = self._decode(fields)
            if msg is not None:
                # Apply filters
                if msg_type is not None and msg.msg_type.value != msg_type:
                    continue
                if sender is not None and msg.sender != sender:
                    continue
                if priority_min is not None and msg.header.priority.value > priority_min:
                    continue
                results.append(msg)
        # Reverse back to chronological order
        return list(reversed(results))

    async def ack(self, msg_id: str) -> None:
        await self._redis.xack(self._stream, self._group, msg_id)

    async def nack(self, msg_id: str, reason: str = "") -> None:
        # Check how many times this message has been delivered
        pending = await self._redis.xpending_range(
            self._stream, self._group, min=msg_id, max=msg_id, count=1
        )
        delivery_count = 1
        if pending:
            delivery_count = pending[0].get("times_delivered", 1)

        if delivery_count >= self._nack_threshold:
            # Move to DLQ and remove from stream
            raw = await self._redis.xrange(self._stream, min=msg_id, max=msg_id, count=1)
            if raw:
                data = raw[0][1].get("data", "{}")
                dlq_entry = {"msg_id": msg_id, "reason": reason, "data": data}
                await self._redis.xadd(
                    self._dlq_key,
                    dlq_entry,
                    maxlen=self._max_dlq,
                    approximate=True,
                )
            await self._redis.xack(self._stream, self._group, msg_id)
            await self._redis.xdel(self._stream, msg_id)
        # If under threshold, leave it in PEL for XCLAIM / redelivery

    async def size(self) -> int:
        return await self._redis.xlen(self._stream)

    async def clear_expired(self) -> int:
        # Redis Streams do not support per-entry TTL.  We scan and delete.
        entries = await self._redis.xrange(self._stream, min="-", max="+")
        removed = 0
        for msg_id, fields in entries:
            msg = self._decode(fields)
            if msg is not None and msg.is_expired:
                await self._redis.xdel(self._stream, msg_id)
                removed += 1
        return removed

    # -- DLQ hooks -----------------------------------------------------------

    async def dlq_size(self) -> int:
        return await self._redis.xlen(self._dlq_key)

    async def dlq_peek(self, limit: int = 5) -> list[dict[str, Any]]:
        entries = await self._redis.xrevrange(self._dlq_key, max="+", min="-", count=limit)
        return [
            {"msg_id": e[0], **e[1]}
            for e in reversed(entries)
        ]

    async def dlq_replay(self, msg_id: str) -> bool:
        """Re-enqueue a dead-letter message from the DLQ stream.

        Reads the message data, deletes it from DLQ, and re-adds to the
        main mailbox stream.
        """
        raw = await self._redis.xrange(self._dlq_key, min=msg_id, max=msg_id, count=1)
        if not raw:
            return False
        _stream_msg_id, fields = raw[0]
        data_str = fields.get("data", "{}")
        await self._redis.xdel(self._dlq_key, msg_id)
        await self._redis.xadd(
            self._stream,
            {"data": data_str},
            maxlen=self._max_size,
            approximate=True,
        )
        return True

    # -- internal helpers ----------------------------------------------------

    async def _ensure_group(self) -> None:
        if self._group_created:
            return
        try:
            await self._redis.xgroup_create(
                self._stream, self._group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "already exists" not in str(exc):
                raise
        self._group_created = True

    @staticmethod
    def _decode(fields: dict[str, str]) -> StructuredMessage | None:
        try:
            data = json.loads(fields.get("data", "{}"))
            return StructuredMessage.from_dict(data)
        except Exception:
            logger.warning("Failed to decode mailbox message: %s", fields.get("data", "")[:200])
            return None
