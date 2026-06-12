"""Tests for RoutingTable."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.transport.routing import (
    RouteEntry,
    RoutingTable,
    TopologyType,
)


@pytest.fixture
def router():
    return RoutingTable()


class TestRoutingTable:
    def test_point_to_point(self, router):
        router.register_agent("a", "coder")
        router.register_agent("b", "reviewer")
        msg = StructuredMessage.from_text("a", "b", "hello")
        topology, targets = router.route(msg)
        assert topology == TopologyType.POINT_TO_POINT
        assert targets == ["b"]

    def test_broadcast(self, router):
        router.register_agent("a", "coder")
        router.register_agent("b", "reviewer")
        msg = StructuredMessage.from_text("director", "*", "all hands")
        topology, targets = router.route(msg)
        assert topology == TopologyType.BROADCAST
        assert set(targets) == {"a", "b"}

    def test_pubsub(self, router):
        router.register_agent("a", "coder")
        router.register_agent("b", "reviewer")
        router.subscribe("a", "file_changed")
        router.subscribe("b", "file_changed")

        msg = StructuredMessage.from_text("director", "topic:file_changed", "x.py changed")
        topology, targets = router.route(msg)
        assert topology == TopologyType.PUB_SUB
        assert set(targets) == {"a", "b"}

    def test_type_multicast(self, router):
        router.register_agent("r1", "reviewer")
        router.register_agent("r2", "reviewer")
        router.register_agent("c1", "coder")

        msg = StructuredMessage.from_text("director", "type:reviewer", "review please")
        topology, targets = router.route(msg)
        assert topology == TopologyType.BROADCAST
        assert set(targets) == {"r1", "r2"}

    def test_pipeline(self, router):
        router.register_agent("a", "coder")
        router.register_agent("b", "reviewer")
        router.register_agent("c", "tester")

        msg = StructuredMessage.from_text("a", "b > c", "pass along")
        topology, targets = router.route(msg)
        assert topology == TopologyType.PIPELINE
        assert targets == ["b", "c"]
        assert router.resolve_pipeline_stages("b > c") == ["b", "c"]

    def test_unregister_removes_agent(self, router):
        router.register_agent("a", "coder")
        router.unregister_agent("a")
        msg = StructuredMessage.from_text("director", "*", "broadcast")
        topology, targets = router.route(msg)
        assert targets == []

    def test_unsubscribe(self, router):
        router.register_agent("a", "coder")
        router.subscribe("a", "alerts")
        router.unsubscribe("a", "alerts")
        msg = StructuredMessage.from_text("director", "topic:alerts", "x")
        topology, targets = router.route(msg)
        assert targets == []

    def test_add_route(self, router):
        router.add_route(RouteEntry(pattern="type:reviewer", topology=TopologyType.BROADCAST))
        assert len(router._routes) == 1

    def test_empty_pubsub_topic(self, router):
        router.register_agent("a", "coder")
        msg = StructuredMessage.from_text("director", "topic:empty", "x")
        topology, targets = router.route(msg)
        assert topology == TopologyType.PUB_SUB
        assert targets == []
