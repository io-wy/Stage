"""Routing table — decide how messages reach agents.

Supports multiple topologies:
- point_to_point: one agent to another
- broadcast: one to all registered agents
- pubsub: topic-based publish/subscribe
- pipeline: chained delivery (A → B → C)
- star: director-centric (legacy default)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from openagents_orchestration.models.message import StructuredMessage


class TopologyType(StrEnum):
    POINT_TO_POINT = "p2p"
    BROADCAST = "broadcast"
    PUB_SUB = "pubsub"
    PIPELINE = "pipeline"


@dataclass
class RouteEntry:
    """A routing rule."""

    pattern: str
    topology: TopologyType
    priority_boost: int = 0


class RoutingTable:
    """Dynamic routing decisions for agent messages."""

    def __init__(self) -> None:
        self._routes: list[RouteEntry] = []
        self._subscriptions: dict[str, set[str]] = {}  # topic -> set of agent_ids
        self._agents: dict[str, Any] = {}

    def register_agent(self, agent_id: str, agent_type: str = "") -> None:
        """Register an agent so it can be a routing target."""
        self._agents[agent_id] = {"agent_type": agent_type}

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from routing targets and all subscriptions."""
        self._agents.pop(agent_id, None)
        for subscribers in self._subscriptions.values():
            subscribers.discard(agent_id)

    def add_route(self, route: RouteEntry) -> None:
        self._routes.append(route)

    def subscribe(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a pubsub topic."""
        self._subscriptions.setdefault(topic, set()).add(agent_id)

    def unsubscribe(self, agent_id: str, topic: str) -> None:
        self._subscriptions.get(topic, set()).discard(agent_id)

    def route(self, msg: StructuredMessage) -> tuple[TopologyType, list[str]]:
        """Return (topology, list of target agent_ids) for a message."""
        recipient = msg.header.recipient

        # Explicit broadcast
        if recipient == "*":
            return TopologyType.BROADCAST, list(self._agents.keys())

        # Topic subscription
        if recipient.startswith("topic:"):
            topic = recipient[6:]
            return TopologyType.PUB_SUB, list(self._subscriptions.get(topic, set()))

        # Agent type multicast
        if recipient.startswith("type:"):
            agent_type = recipient[5:]
            targets = [
                aid for aid, meta in self._agents.items()
                if meta.get("agent_type") == agent_type
            ]
            return TopologyType.BROADCAST if len(targets) > 1 else TopologyType.POINT_TO_POINT, targets

        # Pipeline notation: agent_a > agent_b > agent_c
        if ">" in recipient:
            stages = [s.strip() for s in recipient.split(">")]
            # All stages are returned as ordered targets.  The caller is
            # responsible for linking them via parent_id so each downstream
            # stage can trace back to the original message.
            return TopologyType.PIPELINE, stages

        # Default point-to-point
        return TopologyType.POINT_TO_POINT, [recipient]

    def resolve_pipeline_stages(self, recipient: str) -> list[str]:
        """Return all stages of a pipeline recipient string."""
        if ">" not in recipient:
            return [recipient]
        return [s.strip() for s in recipient.split(">")]

    def get_topic_subscribers(self, topic: str) -> set[str]:
        return set(self._subscriptions.get(topic, set()))
