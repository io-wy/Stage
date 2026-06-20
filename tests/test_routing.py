"""Adversarial + contract tests for the RoutingTable.

Module under test: ``src/openagents_orchestration/transport/routing.py`` (deleted
``test_routing.py``). RoutingTable maps a recipient string to a topology + target
list for StateBoard delivery. ``test_gap_*`` pin surprising routing outcomes.
"""

from __future__ import annotations

from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.transport.routing import RoutingTable, TopologyType


def _to(recipient: str) -> StructuredMessage:
    return StructuredMessage.from_text("sender", recipient, "hi")


# ── contract that works ───────────────────────────────────────────────────────


def test_point_to_point_default():
    rt = RoutingTable()
    topo, targets = rt.route(_to("agent-b"))
    assert topo == TopologyType.POINT_TO_POINT
    assert targets == ["agent-b"]


def test_broadcast_returns_all_registered():
    rt = RoutingTable()
    rt.register_agent("a")
    rt.register_agent("b")
    topo, targets = rt.route(_to("*"))
    assert topo == TopologyType.BROADCAST
    assert set(targets) == {"a", "b"}


def test_pubsub_topic_targets_subscribers():
    rt = RoutingTable()
    rt.subscribe("a", "builds")
    rt.subscribe("b", "builds")
    topo, targets = rt.route(_to("topic:builds"))
    assert topo == TopologyType.PUB_SUB
    assert set(targets) == {"a", "b"}


def test_type_multicast_to_many():
    rt = RoutingTable()
    rt.register_agent("c1", "coder")
    rt.register_agent("c2", "coder")
    rt.register_agent("r1", "reviewer")
    topo, targets = rt.route(_to("type:coder"))
    assert topo == TopologyType.BROADCAST
    assert set(targets) == {"c1", "c2"}


def test_pipeline_stages_in_order():
    rt = RoutingTable()
    topo, targets = rt.route(_to("a > b > c"))
    assert topo == TopologyType.PIPELINE
    assert targets == ["a", "b", "c"]


def test_unregister_removes_from_subscriptions_and_targets():
    rt = RoutingTable()
    rt.register_agent("a")
    rt.subscribe("a", "t")
    rt.unregister_agent("a")
    assert rt.get_topic_subscribers("t") == set()
    _, targets = rt.route(_to("*"))
    assert targets == []


# ── GAP: broadcast echoes back to the sender ──────────────────────────────────


def test_gap_broadcast_includes_the_sender_itself():
    """``route('*')`` returns every registered agent, with no sender exclusion —
    so an agent that broadcasts receives its own message. Combined with a
    resident's pull loop, this can produce self-triggered work."""
    rt = RoutingTable()
    rt.register_agent("a")
    rt.register_agent("b")
    _, targets = rt.route(StructuredMessage.from_text("a", "*", "hello all"))
    assert "a" in targets  # GAP: self-delivery on broadcast


# ── GAP: pipeline parsing keeps empty stages ──────────────────────────────────


def test_gap_pipeline_keeps_empty_stages():
    """``'a >> b'`` (a typo, or a stray ``>``) splits into an empty stage that is
    returned as a routing target, which the delivery layer then tries to reach."""
    rt = RoutingTable()
    topo, targets = rt.route(_to("a >> b"))
    assert topo == TopologyType.PIPELINE
    assert "" in targets  # GAP: empty-string stage routed


# ── GAP: 'type:' topology flips on target cardinality ─────────────────────────


def test_gap_type_multicast_single_match_is_point_to_point():
    """A ``type:`` recipient with exactly one match returns POINT_TO_POINT rather
    than BROADCAST — the topology depends on how many agents happen to match, so
    code keyed on topology sees different types for the same recipient form."""
    rt = RoutingTable()
    rt.register_agent("only", "coder")
    topo, targets = rt.route(_to("type:coder"))
    assert topo == TopologyType.POINT_TO_POINT  # GAP: not BROADCAST despite 'type:'
    assert targets == ["only"]
