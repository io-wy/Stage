"""Tests for StateBoard routing + DLQ integration."""

from __future__ import annotations

import asyncio

import pytest

from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.transport.routing import TopologyType
from openagents_orchestration.core.state_board import Budget, StateBoard


@pytest.fixture
def board():
    return StateBoard("test", budget=Budget())


class TestStateBoardRouting:
    async def test_broadcast_via_routing_table(self, board):
        board.register_agent("a", "coder")
        board.register_agent("b", "reviewer")
        msg = StructuredMessage.from_text("director", "*", "broadcast")
        ok = await board.send_structured(msg)
        assert ok is True

        assert len(await board.claim_messages("a")) == 1
        assert len(await board.claim_messages("b")) == 1

    async def test_pubsub_via_routing_table(self, board):
        board.register_agent("a", "coder")
        board.register_agent("b", "reviewer")
        board.subscribe_topic("a", "alerts")
        board.subscribe_topic("b", "alerts")

        msg = StructuredMessage.from_text("director", "topic:alerts", "fire")
        ok = await board.send_structured(msg)
        assert ok is True

        assert len(await board.claim_messages("a")) == 1
        assert len(await board.claim_messages("b")) == 1

    async def test_type_multicast_via_routing_table(self, board):
        board.register_agent("r1", "reviewer")
        board.register_agent("r2", "reviewer")
        board.register_agent("c1", "coder")

        msg = StructuredMessage.from_text("director", "type:reviewer", "review")
        ok = await board.send_structured(msg)
        assert ok is True

        assert len(await board.claim_messages("r1")) == 1
        assert len(await board.claim_messages("r2")) == 1
        assert len(await board.claim_messages("c1")) == 0

    async def test_unsubscribe(self, board):
        board.register_agent("a", "coder")
        board.subscribe_topic("a", "alerts")
        board.unsubscribe_topic("a", "alerts")

        msg = StructuredMessage.from_text("director", "topic:alerts", "x")
        ok = await board.send_structured(msg)
        assert ok is True
        assert len(await board.claim_messages("a")) == 0

    async def test_unregister_agent_routing(self, board):
        board.register_agent("a", "coder")
        board.unregister_agent("a")

        msg = StructuredMessage.from_text("director", "*", "broadcast")
        ok = await board.send_structured(msg)
        assert ok is True
        # No registered agents
        assert len(board.agents) == 0


class TestStateBoardDLQ:
    async def test_inspect_dlq(self, board):
        board.register_agent("a", "coder")
        msg = StructuredMessage.from_text("director", "a", "fail")
        await board.send_structured(msg)

        for i in range(3):
            claimed = await board.claim_messages("a")
            if claimed:
                await board.nack_message("a", claimed[0].msg_id, "err")
            if i < 2:
                await asyncio.sleep(2 ** i)  # exponential backoff: 1s, 2s

        dlq = await board.inspect_dlq()
        assert "a" in dlq
        assert dlq["a"]["size"] == 1
        assert len(dlq["a"]["latest"]) == 1

    async def test_inspect_dlq_empty(self, board):
        board.register_agent("a", "coder")
        dlq = await board.inspect_dlq()
        assert dlq == {}

    async def test_pipeline_causality_chain(self, board):
        """Pipeline delivery chains messages via parent_id across stages."""
        board.register_agent("a", "coder")
        board.register_agent("b", "reviewer")
        board.register_agent("c", "tester")

        msg = StructuredMessage.from_text("director", "a > b > c", "pipeline test")
        ok = await board.send_structured(msg)
        assert ok is True

        # Stage a: original message, no parent_id
        a_msgs = await board.claim_messages("a")
        assert len(a_msgs) == 1
        assert a_msgs[0].header.parent_id is None

        # Stage b: parent_id = a's msg_id, causality contains a
        b_msgs = await board.claim_messages("b")
        assert len(b_msgs) == 1
        assert b_msgs[0].header.parent_id == a_msgs[0].msg_id
        assert b_msgs[0].header.causality == (a_msgs[0].msg_id,)

        # Stage c: parent_id = b's msg_id, causality = (a, b)
        c_msgs = await board.claim_messages("c")
        assert len(c_msgs) == 1
        assert c_msgs[0].header.parent_id == b_msgs[0].msg_id
        assert c_msgs[0].header.causality == (a_msgs[0].msg_id, b_msgs[0].msg_id)
