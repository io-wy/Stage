"""Tests for distributed tracing helpers."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.models.trace import TraceContext
from openagents_orchestration.core.state_board import Budget, StateBoard


class TestTraceContext:
    def test_default_creation(self):
        t = TraceContext()
        assert t.trace_id.startswith("trace_")
        assert t.span_id.startswith("span_")
        assert t.parent_span_id is None

    def test_child_preserves_trace(self):
        root = TraceContext()
        child = root.child("coder-step")
        assert child.trace_id == root.trace_id
        assert child.span_id.startswith("span_")
        assert child.parent_span_id == root.span_id
        assert "coder-step" in child.span_id

    def test_roundtrip_dict(self):
        t = TraceContext()
        d = t.to_dict()
        restored = TraceContext.from_dict(d)
        assert restored == t

    def test_inject_into_message(self):
        root = TraceContext()
        msg = StructuredMessage.from_text("a", "b", "hello")
        assert not msg.header.trace_id
        root.inject_into_message(msg)
        assert msg.header.trace_id == root.trace_id
        assert msg.header.parent_span_id == root.span_id


class TestStateBoardTracing:
    async def test_start_trace(self):
        board = StateBoard("test", budget=Budget())
        trace = board.start_trace("t1")
        assert trace.trace_id.startswith("trace_")
        assert board.get_trace("t1") == trace

    def test_child_trace(self):
        board = StateBoard("test", budget=Budget())
        parent = board.start_trace("t1")
        child = board.start_trace("coder-t1", parent=parent)
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id

    async def test_trace_propagated_in_message(self):
        board = StateBoard("test", budget=Budget())
        board.register_agent("coder-1", "coder")
        board.start_trace("coder-1")

        msg = StructuredMessage.from_text("director", "coder-1", "do work")
        assert not msg.header.trace_id
        await board.send_structured(msg)
        assert msg.header.trace_id  # propagated

    async def test_trace_propagated_from_task(self):
        board = StateBoard("test", budget=Budget())
        from openagents_orchestration.models.task import TaskNode, TaskStatus
        task = TaskNode(task_id="t1", description="test", agent_type="coder")
        board.tasks["t1"] = task
        board.register_agent("coder-t1", "coder")
        parent = board.start_trace("t1")
        # Agent trace is typically a child of the task trace (as runner.py does)
        board.start_trace("coder-t1", parent=parent)

        msg = StructuredMessage.from_text("director", "coder-t1", "do task t1")
        await board.send_structured(msg)
        assert msg.header.trace_id

    async def test_trace_logged_in_event(self):
        board = StateBoard("test", budget=Budget())
        board.start_trace("t1")
        board.log_event("task.started", task_id="t1")

        # Find the task event with trace_id (exclude trace.started)
        traced = [
            e
            for e in board.events
            if e.event_type == "task.started" and e.payload.get("trace_id")
        ]
        assert len(traced) == 1
        assert traced[0].payload["trace_id"].startswith("trace_")

    def test_propagate_trace_does_not_override_existing(self):
        board = StateBoard("test", budget=Budget())
        board.register_agent("coder-1", "coder")
        board.start_trace("coder-1")

        existing_trace = TraceContext()
        msg = StructuredMessage.from_text("director", "coder-1", "hello")
        object.__setattr__(msg.header, "trace_id", existing_trace.trace_id)

        board.propagate_trace(msg)
        assert msg.header.trace_id == existing_trace.trace_id
