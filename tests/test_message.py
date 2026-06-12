"""Tests for StructuredMessage and MessageHeader."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.message import (
    MessageHeader,
    MessageType,
    Priority,
    StructuredMessage,
)


class TestMessageHeader:
    def test_default_creation(self):
        h = MessageHeader()
        assert h.msg_id
        assert h.msg_type == MessageType.NOTIFICATION
        assert h.priority == Priority.NORMAL
        assert h.delivery_count == 0
        assert not h.is_expired

    def test_with_delivery_increments(self):
        h = MessageHeader(delivery_count=2)
        h2 = h.with_delivery()
        assert h2.delivery_count == 3
        # Original is immutable
        assert h.delivery_count == 2

    def test_expired_with_ttl(self):
        import time
        from datetime import datetime, timedelta, timezone

        h = MessageHeader(
            created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            ttl_s=5.0,
        )
        assert h.is_expired

    def test_not_expired_without_ttl(self):
        h = MessageHeader()
        assert not h.is_expired


class TestStructuredMessage:
    def test_from_text_back_compat(self):
        msg = StructuredMessage.from_text("director", "coder-1", "hello")
        assert msg.text == "hello"
        assert msg.sender == "director"
        assert msg.recipient == "coder-1"
        assert msg.msg_type == MessageType.NOTIFICATION

    def test_command_factory(self):
        msg = StructuredMessage.command(
            "director", "coder-1", "implement", feature="auth"
        )
        assert msg.header.msg_type == MessageType.COMMAND
        assert msg.header.priority == Priority.HIGH
        assert msg.payload["action"] == "implement"
        assert msg.payload["params"]["feature"] == "auth"

    def test_signal_factory(self):
        msg = StructuredMessage.signal(
            "coder-t1", "reviewer-t1", "review_ready", "t1", tests_passed=5
        )
        assert msg.header.msg_type == MessageType.SIGNAL
        assert msg.payload["signal"] == "review_ready"
        assert msg.payload["tests_passed"] == 5

    def test_event_factory(self):
        msg = StructuredMessage.event("coder-1", "task_complete", task_id="t1")
        assert msg.header.msg_type == MessageType.EVENT
        assert msg.header.recipient == "*"
        assert msg.is_broadcast

    def test_roundtrip_serialization(self):
        msg = StructuredMessage.command(
            "director", "coder-1", "test", value=42
        )
        d = msg.to_dict()
        restored = StructuredMessage.from_dict(d)
        assert restored.header.msg_id == msg.header.msg_id
        assert restored.header.msg_type == msg.header.msg_type
        assert restored.header.priority == msg.header.priority
        assert restored.payload == msg.payload
        assert restored.text == msg.text

    def test_broadcast_detection(self):
        msg = StructuredMessage.from_text("a", "*", "broadcast")
        assert msg.is_broadcast

        msg2 = StructuredMessage.from_text("a", "b", "direct")
        assert not msg2.is_broadcast
