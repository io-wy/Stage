"""Tests for ResidentAgent and resident management."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


from openagents_orchestration.core.resident import ResidentAgent, ResidentState
from openagents_orchestration.core.state_board import StateBoard


class MockRunner:
    def __init__(self):
        self.calls: list[dict] = []

    async def _run_resident_single(self, **kwargs):
        self.calls.append(kwargs)
        result = MagicMock()
        result.final_output = "Done. FILES_CREATED: output.py"
        result.metadata = {
            "transcript": [
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "Done"},
            ]
        }
        result.usage = MagicMock()
        result.usage.total_tokens = 150
        return result


class TestResidentState:
    def test_to_dict(self):
        rs = ResidentState(
            resident_id="coder-r1",
            agent_type="coder",
            status="idle",
            token_used=1000,
            message_count=5,
        )
        d = rs.to_dict()
        assert d["resident_id"] == "coder-r1"
        assert d["agent_type"] == "coder"
        assert d["status"] == "idle"
        assert d["token_used"] == 1000
        assert d["message_count"] == 5
        assert "uptime_s" in d


class TestResidentAgent:
    def test_build_input(self):
        msg = {
            "from": "director",
            "task": "fix bug",
            "content": "line 42 is wrong",
            "context": "auth.py",
        }
        inp = ResidentAgent._build_input(msg)
        assert "fix bug" in inp
        assert "director" in inp
        assert "line 42 is wrong" in inp
        assert "auth.py" in inp

    def test_build_input_minimal(self):
        msg = {"from": "director", "task": "hello"}
        inp = ResidentAgent._build_input(msg)
        assert "hello" in inp

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        board = StateBoard("obj")
        runner = MockRunner()
        resident = ResidentAgent(
            resident_id="coder-r1",
            agent_type="coder",
            runner=runner,
            board=board,
            max_idle_s=1.0,
        )

        # Start
        await resident.start()
        assert resident._active is True
        assert "coder-r1" in board.residents

        # Send a message
        await resident.send({
            "from": "director",
            "task": "write hello.py",
            "content": "print hello world",
        })

        # Wait for processing
        await asyncio.sleep(0.5)

        # Stop
        await resident.stop()
        assert resident._active is False

        # Verify runner was called
        assert len(runner.calls) == 1
        assert runner.calls[0]["resident_id"] == "coder-r1"
        assert "hello.py" in runner.calls[0]["input_text"]

        # Verify reply was sent via Mailbox v2
        replies = await board.claim_messages("director")
        assert len(replies) >= 1
        reply = replies[-1]
        assert reply.header.sender == "coder-r1"
        assert reply.header.recipient == "director"

    @pytest.mark.asyncio
    async def test_idle_timeout(self):
        board = StateBoard("obj")
        runner = MockRunner()
        resident = ResidentAgent(
            resident_id="coder-r1",
            agent_type="coder",
            runner=runner,
            board=board,
            max_idle_s=0.1,
        )

        await resident.start()
        # Wait for idle timeout
        await asyncio.sleep(0.3)

        assert resident._active is False
        assert board.residents["coder-r1"].status == "stopped"

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        board = StateBoard("obj")
        runner = MockRunner()
        resident = ResidentAgent(
            resident_id="coder-r1",
            agent_type="coder",
            runner=runner,
            board=board,
            max_idle_s=1.0,
        )

        await resident.start()
        await resident.send({"from": "director", "task": "task 1"})
        await resident.send({"from": "director", "task": "task 2"})
        await asyncio.sleep(0.3)
        await resident.stop()

        # Should have processed both messages
        assert len(runner.calls) == 2
        # Transcript should be persistent (accumulated)
        assert len(resident._transcript) > 0

    @pytest.mark.asyncio
    async def test_sleep_backlog_circular_buffer(self):
        board = StateBoard("obj")
        runner = MockRunner()
        resident = ResidentAgent(
            resident_id="coder-r1",
            agent_type="coder",
            runner=runner,
            board=board,
            max_idle_s=10.0,
        )

        await resident.start()
        await resident.sleep("waiting for review")

        # Send 102 messages while sleeping
        for i in range(102):
            resident.send_nowait({"from": "director", "content": f"msg-{i}"})

        await asyncio.sleep(0.1)

        # Wake and let it process
        await resident.wake()
        await asyncio.sleep(0.5)
        await resident.stop()

        # Backlog should keep the 100 most recent, dropping msg-0 and msg-1
        assert len(resident._sleep_backlog) == 0  # drained on wake
        dropped_events = [e for e in board.events if e.event_type == "resident.backlog_dropped"]
        assert len(dropped_events) == 2
        # Verify the oldest remaining message in processed order would be msg-2
        assert runner.calls[0]["input_text"].startswith("# Message from director\nmsg-2")

    @pytest.mark.asyncio
    async def test_max_steps_with_artifacts_signals_review_ready(self):
        board = StateBoard("obj")
        runner = MockRunner()

        artifact = MagicMock()
        artifact.path = "service_eval/app.py"
        result = MagicMock()
        result.final_output = "[CoreCoder] step budget exhausted before producing a final answer."
        result.metadata = {"transcript": []}
        result.usage = MagicMock()
        result.usage.total_tokens = 100
        result.stop_reason = type("Stop", (), {"value": "max_steps"})()
        result.artifacts = [artifact]
        runner._run_resident_single = AsyncMock(return_value=result)

        resident = ResidentAgent(
            resident_id="coder-t1",
            agent_type="coder",
            runner=runner,
            board=board,
            max_idle_s=1.0,
        )
        resident.bind_task("t1")

        await resident.start()
        await resident.send({"from": "director", "task": "implement service"})
        await asyncio.sleep(0.2)
        await resident.stop()

        messages = await board.claim_messages("reviewer-t1")
        assert len(messages) == 1
        assert messages[0].header.sender == "coder-t1"
        assert messages[0].msg_type == "signal"
        assert messages[0].payload["signal"] == "review_ready"
        assert messages[0].payload["task_id"] == "t1"
        assert "TASK_REVIEW_READY[t1]" in messages[0].text
        assert board.artifacts["service_eval/app.py"].status == "verified"

    @pytest.mark.asyncio
    async def test_error_handling(self):
        board = StateBoard("obj")
        runner = MockRunner()
        runner._run_resident_single = AsyncMock(side_effect=RuntimeError("LLM failed"))

        resident = ResidentAgent(
            resident_id="coder-r1",
            agent_type="coder",
            runner=runner,
            board=board,
            max_idle_s=1.0,
        )

        await resident.start()
        await resident.send({"from": "director", "task": "task"})
        await asyncio.sleep(0.2)
        await resident.stop()

        # Error should be recorded
        assert board.residents["coder-r1"].error_count == 1
        assert board.residents["coder-r1"].status == "stopped"
        # Error reply should be sent via Mailbox v2
        replies = await board.claim_messages("director")
        assert len(replies) >= 1


class TestStateBoardResidents:
    def test_register_and_get(self):
        board = StateBoard("obj")
        rs = ResidentState(resident_id="r1", agent_type="coder")
        board.register_resident(rs)
        assert board.get_resident("r1").agent_type == "coder"

    def test_update(self):
        board = StateBoard("obj")
        board.register_resident(ResidentState(resident_id="r1", agent_type="coder"))
        board.update_resident("r1", status="busy", token_used=100)
        assert board.get_resident("r1").status == "busy"
        assert board.get_resident("r1").token_used == 100

    def test_list_by_type(self):
        board = StateBoard("obj")
        board.register_resident(ResidentState(resident_id="r1", agent_type="coder"))
        board.register_resident(ResidentState(resident_id="r2", agent_type="coder"))
        board.register_resident(ResidentState(resident_id="r3", agent_type="reviewer"))

        coders = board.list_residents("coder")
        assert len(coders) == 2

    def test_snapshot_includes_residents(self):
        board = StateBoard("obj")
        board.register_resident(ResidentState(resident_id="r1", agent_type="coder"))
        snapshot = board.snapshot()
        assert "residents" in snapshot
        assert len(snapshot["residents"]) == 1

    def test_report_includes_residents(self):
        board = StateBoard("obj")
        board.register_resident(ResidentState(resident_id="r1", agent_type="coder"))
        report = board.to_report()
        assert "residents" in report.metadata
