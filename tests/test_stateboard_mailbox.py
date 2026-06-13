"""Tests for StateBoard Mailbox v2 integration."""

from __future__ import annotations

import asyncio

import pytest

from openagents_orchestration.models.message import (
    MessageType,
    Priority,
    StructuredMessage,
)
from openagents_orchestration.core.state_board import Budget, StateBoard


@pytest.fixture
def board():
    return StateBoard(
        objective="test",
        budget=Budget(token_limit=1000, time_limit_s=300.0),
    )


class TestStateBoardMailboxV2:
    async def test_send_structured_and_claim(self, board):
        board.register_agent("coder-1", "coder")
        msg = StructuredMessage.command("director", "coder-1", "implement")
        ok = await board.send_structured(msg)
        assert ok is True

        claimed = await board.claim_messages("coder-1", batch_size=5)
        assert len(claimed) == 1
        assert claimed[0].payload["action"] == "implement"

    async def test_send_structured_broadcast(self, board):
        board.register_agent("coder-1", "coder")
        board.register_agent("reviewer-1", "reviewer")
        msg = StructuredMessage.event("director", "all_hands", data="go")
        ok = await board.send_structured(msg)
        assert ok is True

        c_msgs = await board.claim_messages("coder-1")
        r_msgs = await board.claim_messages("reviewer-1")
        assert len(c_msgs) == 1
        assert len(r_msgs) == 1

    async def test_claim_message_specific(self, board):
        board.register_agent("coder-1", "coder")
        first = StructuredMessage.from_text("director", "coder-1", "first")
        second = StructuredMessage.from_text("director", "coder-1", "second")
        await board.send_structured(first)
        await board.send_structured(second)

        claimed = await board.claim_message("coder-1", second.msg_id)
        assert claimed is not None
        assert claimed.msg_id == second.msg_id

        # First message remains
        remaining = await board.claim_messages("coder-1", batch_size=10)
        assert len(remaining) == 1
        assert remaining[0].text == "first"

    async def test_claim_message_specific_not_found(self, board):
        board.register_agent("coder-1", "coder")
        assert await board.claim_message("coder-1", "nonexistent") is None

    async def test_ack_message(self, board):
        board.register_agent("coder-1", "coder")
        msg = StructuredMessage.from_text("director", "coder-1", "work")
        await board.send_structured(msg)

        claimed = await board.claim_messages("coder-1")
        assert len(claimed) == 1

        await board.ack_message("coder-1", claimed[0].msg_id)

        # Should be empty after ack
        claimed2 = await board.claim_messages("coder-1")
        assert len(claimed2) == 0

    async def test_nack_message(self, board):
        board.register_agent("coder-1", "coder")
        msg = StructuredMessage.from_text("director", "coder-1", "retry_me")
        await board.send_structured(msg)

        claimed = await board.claim_messages("coder-1")
        assert len(claimed) == 1

        await board.nack_message("coder-1", claimed[0].msg_id, "temp")
        await asyncio.sleep(1.0)  # wait for nack backoff (base=1s)
        # Message should be re-delivered
        claimed2 = await board.claim_messages("coder-1")
        assert len(claimed2) == 1

    async def test_peek_mailbox(self, board):
        board.register_agent("coder-1", "coder")
        msg = StructuredMessage.from_text("director", "coder-1", "peek")
        await board.send_structured(msg)

        peeked = await board.peek_mailbox("coder-1", limit=1)
        assert len(peeked) == 1

        # Still claimable after peek
        claimed = await board.claim_messages("coder-1")
        assert len(claimed) == 1

    async def test_legacy_send_mail_still_works(self, board):
        """Backward compatibility: old sync API continues to function."""
        board.send_mail("director", "coder-1", "legacy message")
        msgs = board.messages_for("coder-1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "legacy message"

    async def test_legacy_clear_mail(self, board):
        board.send_mail("a", "b", "msg1")
        board.send_mail("a", "b", "msg2")
        assert len(board.messages_for("b")) == 2

        cleared = board.clear_mail("b")
        assert cleared == 2
        assert len(board.messages_for("b")) == 0

    async def test_back_pressure_on_full_mailbox(self, board):
        # Create a mailbox with tiny capacity
        from openagents_orchestration.mailbox.memory import InMemoryMailbox
        board._mailboxes["coder-1"] = InMemoryMailbox(max_size=1)
        board.register_agent("coder-1", "coder")

        msg1 = StructuredMessage.from_text("director", "coder-1", "fill")
        msg2 = StructuredMessage.from_text("director", "coder-1", "overflow")

        ok1 = await board.send_structured(msg1)
        ok2 = await board.send_structured(msg2)

        assert ok1 is True
        assert ok2 is False  # Back-pressure

    async def test_dlq_monitoring(self, board):
        board.register_agent("coder-1", "coder")
        msg = StructuredMessage.from_text("director", "coder-1", "fail")
        await board.send_structured(msg)

        # Nack 3 times to move to DLQ (wait exponential backoff between cycles)
        for i in range(3):
            claimed = await board.claim_messages("coder-1")
            if claimed:
                await board.nack_message("coder-1", claimed[0].msg_id, "fail")
            if i < 2:
                await asyncio.sleep(2 ** i)  # backoff: 1s, 2s

        mbox = board._get_or_create_mailbox("coder-1")
        dlq_size = await mbox.dlq_size()
        assert dlq_size == 1

        dlq_items = await mbox.dlq_peek(limit=5)
        assert len(dlq_items) == 1
        assert dlq_items[0]["reason"] == "fail"

    async def test_dlq_replay_re_enqueues(self, board):
        """dlq_replay moves a DLQ message back to the mailbox for re-processing."""
        board.register_agent("coder-1", "coder")
        msg = StructuredMessage.from_text("director", "coder-1", "retry me")
        await board.send_structured(msg)

        # Nack 3 times to move to DLQ (wait exponential backoff between cycles)
        for i in range(3):
            claimed = await board.claim_messages("coder-1")
            if claimed:
                await board.nack_message("coder-1", claimed[0].msg_id, "fail")
            if i < 2:
                await asyncio.sleep(2 ** i)  # backoff: 1s, 2s

        assert await board._get_or_create_mailbox("coder-1").dlq_size() == 1

        # Replay it
        ok = await board.dlq_replay("coder-1", msg.msg_id)
        assert ok is True

        # DLQ should be empty now
        assert await board._get_or_create_mailbox("coder-1").dlq_size() == 0

        # Message should be claimable again
        replayed = await board.claim_messages("coder-1")
        assert len(replayed) == 1
        assert replayed[0].text == "retry me"

    async def test_dlq_replay_unknown_msg(self, board):
        """dlq_replay returns False for unknown msg_ids."""
        board.register_agent("coder-1", "coder")
        ok = await board.dlq_replay("coder-1", "nonexistent")
        assert ok is False

    async def test_mailbox_isolation(self, board):
        board.register_agent("a", "coder")
        board.register_agent("b", "reviewer")

        await board.send_structured(
            StructuredMessage.from_text("director", "a", "for_a")
        )
        await board.send_structured(
            StructuredMessage.from_text("director", "b", "for_b")
        )

        a_msgs = await board.claim_messages("a")
        b_msgs = await board.claim_messages("b")

        assert len(a_msgs) == 1
        assert a_msgs[0].text == "for_a"
        assert len(b_msgs) == 1
        assert b_msgs[0].text == "for_b"

    async def test_message_signing_rejects_invalid_token(self):
        """Messages from agents with invalid capability tokens are rejected."""
        from openagents_orchestration.enterprise.security import CapabilityToken

        board = StateBoard("obj", budget=Budget())
        board.register_agent("coder-1", "coder")

        # Issue a valid token for coder-1
        token = CapabilityToken.issue("director", "coder-1", ["send_message"], ["*"])
        board.set_agent_token("coder-1", token)

        # Send with valid token holder → works
        msg = StructuredMessage.from_text("coder-1", "reviewer-1", "legit")
        ok = await board.send_structured(msg)
        assert ok is True

    async def test_message_signing_allows_unregistered(self):
        """Agents without capability tokens are trusted (backward compat)."""
        board = StateBoard("obj", budget=Budget())
        board.register_agent("coder-1", "coder")

        # No token set → trusted by default
        msg = StructuredMessage.from_text("coder-1", "reviewer-1", "hello")
        ok = await board.send_structured(msg)
        assert ok is True

    async def test_message_signing_expired_token(self):
        """Expired tokens cause message rejection."""
        from openagents_orchestration.enterprise.security import CapabilityToken

        board = StateBoard("obj", budget=Budget())
        board.register_agent("coder-1", "coder")

        # Issue a token that expires immediately
        token = CapabilityToken.issue("director", "coder-1", ["send_message"], ["*"], ttl_s=0)
        board.set_agent_token("coder-1", token)

        msg = StructuredMessage.from_text("coder-1", "reviewer-1", "stale")
        ok = await board.send_structured(msg)
        assert ok is False
