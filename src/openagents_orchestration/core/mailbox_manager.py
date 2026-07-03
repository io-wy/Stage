"""MailboxManager — owns mailboxes, routing, channel policy, and delivery.

Extracted from StateBoard so the board can focus on orchestration state
(tasks, agents, budget, events) while message transport lives in its own
component. StateBoard keeps thin adapter methods for backward compatibility.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
from typing import Any

from openagents_orchestration.mailbox.base import Mailbox
from openagents_orchestration.mailbox.memory import InMemoryMailbox
from openagents_orchestration.models.message import MessageHeader, StructuredMessage
from openagents_orchestration.transport.channel_policy import (
    DEFAULT_GLOBAL_POLICY,
    ChannelPolicy,
    ChannelPolicyError,
)
from openagents_orchestration.transport.routing import RoutingTable, TopologyType

# Module-level thread pool singleton for sync bridge calls.
_sync_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mm-sync")


class MailboxManager:
    """Per-agent queues, routing, and policy enforcement."""

    def __init__(
        self,
        *,
        mailbox_backend: str = "memory",
        redis_url: str | None = None,
        channel_policy: ChannelPolicy | None = None,
    ):
        self._mailbox_backend = mailbox_backend
        self._redis_url = redis_url
        self._redis_client: Any = None
        self._mailboxes: dict[str, Mailbox] = {}
        self._mailbox_cls = InMemoryMailbox
        if mailbox_backend == "redis" and redis_url:
            self._init_redis(redis_url)

        self._routing_table = RoutingTable()
        self._channel_policy = channel_policy or DEFAULT_GLOBAL_POLICY
        self._mail_sent_callbacks: list[Any] = []

    def on_mail_sent(self, callback: Any) -> None:
        """Register a callback invoked after a message is successfully sent."""
        self._mail_sent_callbacks.append(callback)

    # -- redis backend -------------------------------------------------------

    def _init_redis(self, redis_url: str) -> None:
        """Attempt to connect to Redis; fall back to memory on failure."""
        try:
            import redis.asyncio as aioredis
            self._redis_client = aioredis.from_url(redis_url, decode_responses=True)
            self._mailbox_cls = None  # type: ignore[assignment]
        except (ImportError, ValueError, OSError, ConnectionError) as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Redis init failed for %s (%s: %s), falling back to memory",
                redis_url, type(exc).__name__, exc,
            )
            self._mailbox_backend = "memory"
            self._redis_client = None

    async def validate_redis(self) -> bool:
        """Ping Redis if configured; downgrade to memory on failure."""
        if self._mailbox_backend != "redis" or self._redis_client is None:
            return True
        try:
            await self._redis_client.ping()
            return True
        except (OSError, ConnectionError, TimeoutError) as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Redis unreachable (%s: %s), falling back to in-memory mailbox",
                type(exc).__name__, exc,
            )
            await self.close()
            self._mailbox_backend = "memory"
            self._redis_client = None
            return False

    async def close(self) -> None:
        """Close any open Redis connection held by this manager."""
        if self._redis_client is not None:
            with contextlib.suppress(Exception):
                await self._redis_client.close()
            self._redis_client = None

    def _get_or_create_mailbox(self, agent_id: str) -> Mailbox:
        """Return the mailbox for an agent, creating it if necessary."""
        if agent_id not in self._mailboxes:
            if self._mailbox_backend == "redis" and self._redis_client is not None:
                from openagents_orchestration.mailbox.redis import RedisMailbox
                self._mailboxes[agent_id] = RedisMailbox(
                    self._redis_client, agent_id
                )
            else:
                self._mailboxes[agent_id] = InMemoryMailbox()
        return self._mailboxes[agent_id]

    # -- routing --------------------------------------------------------------

    def register_agent(self, agent_id: str, agent_type: str = "") -> None:
        """Register an agent so it can be a routing target."""
        self._routing_table.register_agent(agent_id, agent_type)

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from routing targets and all subscriptions."""
        self._routing_table.unregister_agent(agent_id)

    def subscribe_topic(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a pubsub topic."""
        self._routing_table.subscribe(agent_id, topic)

    def unsubscribe_topic(self, agent_id: str, topic: str) -> None:
        """Unsubscribe an agent from a pubsub topic."""
        self._routing_table.unsubscribe(agent_id, topic)

    def add_route(self, pattern: str, topology: str, *, priority_boost: int = 0) -> None:
        """Add a routing rule (e.g. pattern='type:reviewer', topology='broadcast')."""
        from openagents_orchestration.transport.routing import RouteEntry
        self._routing_table.add_route(
            RouteEntry(pattern=pattern, topology=topology, priority_boost=priority_boost)
        )

    # -- delivery core --------------------------------------------------------

    async def enqueue(
        self,
        msg: StructuredMessage,
        *,
        bypass_policy: bool = False,
    ) -> bool:
        """Route and deliver a message to target mailboxes.

        When *bypass_policy* is True, channel policy is skipped — used by legacy
        send_mail and event replayers that must deliver unconditionally.
        """
        if not bypass_policy:
            try:
                self._channel_policy.assert_allowed(msg.header.sender, msg.header.recipient)
            except ChannelPolicyError:
                return False

        topology, targets = self._routing_table.route(msg)
        if not targets:
            # No registered recipients; create a mailbox on-the-fly for the
            # explicit recipient so the message is not silently dropped.
            targets = [msg.header.recipient]

        ok = await self._deliver_to_targets(msg, topology, targets)
        for cb in self._mail_sent_callbacks:
            with contextlib.suppress(Exception):
                cb()
        return ok

    async def _deliver_to_targets(
        self,
        msg: StructuredMessage,
        topology: TopologyType,
        targets: list[str],
    ) -> bool:
        """Deliver a message to resolved targets."""
        self._populate_causality_if_needed(msg)

        success = True
        chain_causality = msg.header.causality
        parent_id = msg.msg_id
        for agent_id in targets:
            if topology == TopologyType.PIPELINE and agent_id != targets[0]:
                chain_causality = chain_causality + (parent_id,)
                payload = dict(msg.payload)
                payload["pipeline_stage"] = agent_id
                chain_msg = StructuredMessage(
                    header=MessageHeader(
                        sender=msg.header.sender, recipient=agent_id,
                        msg_type=msg.header.msg_type, priority=msg.header.priority,
                        parent_id=parent_id, trace_id=msg.header.trace_id,
                        causality=chain_causality,
                    ),
                    payload=payload, text=msg.text,
                )
                parent_id = chain_msg.msg_id
                mbox = self._get_or_create_mailbox(agent_id)
                ok = await mbox.enqueue(chain_msg)
            else:
                mbox = self._get_or_create_mailbox(agent_id)
                ok = await mbox.enqueue(msg)
            success = success and ok
        return success

    @staticmethod
    def _populate_causality_if_needed(msg: StructuredMessage) -> None:
        """Populate the causality chain when a message is a reply (has parent_id)."""
        if msg.header.parent_id and not msg.header.causality:
            msg.header = MessageHeader(
                msg_id=msg.header.msg_id,
                parent_id=msg.header.parent_id,
                trace_id=msg.header.trace_id,
                parent_span_id=msg.header.parent_span_id,
                causality=(msg.header.parent_id,),
                idempotency_key=msg.header.idempotency_key,
                sender=msg.header.sender,
                recipient=msg.header.recipient,
                msg_type=msg.header.msg_type,
                priority=msg.header.priority,
                created_at=msg.header.created_at,
                ttl_s=msg.header.ttl_s,
                delivery_count=msg.header.delivery_count,
            )

    def enqueue_sync(self, msg: StructuredMessage, *, bypass_policy: bool = False) -> bool:
        """Synchronous wrapper around ``enqueue``.

        Uses a module-level thread pool to avoid nested event loops.
        """
        return self._run_sync(self.enqueue(msg, bypass_policy=bypass_policy))

    @staticmethod
    def _run_sync(coro: Any) -> Any:
        """Run a coroutine synchronously, handling nested event loops."""
        try:
            asyncio.get_running_loop()
            future = _sync_pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
        except RuntimeError:
            return asyncio.run(coro)

    # -- legacy sync API ------------------------------------------------------

    def send_mail(self, from_id: str, to_id: str, content: str) -> None:
        """Synchronous legacy API — delivers unconditionally (bypasses policy)."""
        msg = StructuredMessage.from_text(from_id, to_id, content)
        self.enqueue_sync(msg, bypass_policy=True)

    def messages_for(self, recipient: str) -> list[dict[str, Any]]:
        """Synchronous legacy API — returns plain dicts from the mailbox."""
        mbox = self._get_or_create_mailbox(recipient)
        if isinstance(mbox, InMemoryMailbox):
            structured = mbox._sync_peek(limit=1000)
        else:
            structured = self._run_sync(mbox.peek(limit=1000))

        return [
            {
                "from": msg.sender,
                "to": msg.recipient,
                "content": msg.text,
                "ts": msg.header.created_at.timestamp(),
            }
            for msg in structured
        ]

    def clear_mail(self, recipient: str | None = None) -> int:
        """Clear mailbox queues for one or all agents."""
        cleared = 0
        targets = list(self._mailboxes.keys()) if recipient is None else [recipient]

        for agent_id in targets:
            mbox = self._mailboxes.get(agent_id)
            if mbox is None:
                continue
            if isinstance(mbox, InMemoryMailbox):
                cleared += mbox._sync_clear()
            else:
                messages = self._run_sync(mbox.peek(limit=10_000))
                for msg in messages:
                    self._run_sync(mbox.ack(msg.msg_id))
                    cleared += 1
        return cleared

    # -- async API ------------------------------------------------------------

    async def send_structured(self, msg: StructuredMessage) -> bool:
        """New async API — policy-aware delivery with back-pressure."""
        return await self.enqueue(msg, bypass_policy=False)

    async def peek_mailbox(
        self,
        recipient: str,
        limit: int = 5,
        *,
        msg_type: str | None = None,
        sender: str | None = None,
        priority_min: int | None = None,
    ) -> list[StructuredMessage]:
        """Peek into the recipient's mailbox without consuming."""
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.peek(
            limit=limit, msg_type=msg_type, sender=sender, priority_min=priority_min,
        )

    async def claim_message(self, recipient: str, msg_id: str) -> StructuredMessage | None:
        """Pull a specific message from the mailbox by msg_id."""
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.dequeue_specific(msg_id)

    async def claim_messages(self, recipient: str, batch_size: int = 10) -> list[StructuredMessage]:
        """Pull messages from the mailbox (dequeue). Caller must ack() them."""
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.dequeue(batch_size=batch_size)

    async def ack_message(self, recipient: str, msg_id: str) -> None:
        """Acknowledge a message as fully processed."""
        mbox = self._get_or_create_mailbox(recipient)
        await mbox.ack(msg_id)

    async def nack_message(self, recipient: str, msg_id: str, reason: str = "") -> None:
        """Negative-acknowledge — triggers re-delivery or DLQ."""
        mbox = self._get_or_create_mailbox(recipient)
        await mbox.nack(msg_id, reason)

    async def dlq_replay(self, recipient: str, msg_id: str) -> bool:
        """Re-enqueue a dead-lettered message for the given recipient."""
        mbox = self._get_or_create_mailbox(recipient)
        return await mbox.dlq_replay(msg_id)

    async def inspect_dlq(self) -> dict[str, dict[str, Any]]:
        """Return DLQ summary per agent."""
        result: dict[str, dict[str, Any]] = {}
        for agent_id, mbox in self._mailboxes.items():
            size = await mbox.dlq_size()
            if size:
                result[agent_id] = {
                    "size": size,
                    "latest": await mbox.dlq_peek(limit=3),
                }
        return result

    def aggregate_mailbox_metrics(self) -> dict[str, Any]:
        """Return aggregated mailbox metrics across all agents."""
        total = {
            "mailbox_count": 0,
            "total_enqueued": 0,
            "total_dequeued": 0,
            "total_acked": 0,
            "total_nacked": 0,
            "total_enqueue_rejected": 0,
            "total_expired": 0,
            "total_dlq_size": 0,
            "total_dlq_moved": 0,
            "total_dlq_replayed": 0,
            "per_agent": {},
        }
        for agent_id, mbox in self._mailboxes.items():
            total["mailbox_count"] += 1
            metrics = getattr(mbox, "metrics", None)
            if metrics is not None:
                snap = metrics.snapshot()
                total["total_enqueued"] += snap["enqueued"]
                total["total_dequeued"] += snap["dequeued"]
                total["total_acked"] += snap["acked"]
                total["total_nacked"] += snap["nacked"]
                total["total_enqueue_rejected"] += snap["enqueue_rejected"]
                total["total_expired"] += snap["expired"]
                total["total_dlq_moved"] += snap["dlq_moved"]
                total["total_dlq_replayed"] += snap["dlq_replayed"]
                total["per_agent"][agent_id] = snap
            cb_state = getattr(mbox, "_circuit_state", None)
            if cb_state and cb_state != "closed":
                total["per_agent"][agent_id] = total["per_agent"].get(agent_id, {})
                total["per_agent"][agent_id]["circuit_state"] = cb_state
        return total
