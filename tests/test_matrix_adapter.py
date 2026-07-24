"""Test Matrix adapter logic without real Matrix or orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("nio")

from openagents_orchestration.im_adapters.matrix import MatrixAdapter
from openagents_orchestration.models.delivery import DeliveryReport
from openagents_orchestration.runtime.global_orchestrator import GlobalOrchestrator


class FakeRoom:
    room_id = "!test:example.org"


class FakeEvent:
    def __init__(self, body: str, sender: str = "@user:example.org"):
        self.body = body
        self.sender = sender
        self.source = {}


@pytest.fixture
async def adapter() -> MatrixAdapter:
    """Build a MatrixAdapter with mocked Matrix client and stubbed orchestrator."""
    orig_run = GlobalOrchestrator.run

    async def _fake_run(self, objective: str, **kw: object) -> DeliveryReport:
        return DeliveryReport(objective=objective, final_output=f"OK: {objective[:100]}")

    GlobalOrchestrator.run = _fake_run  # type: ignore[assignment]

    a = MatrixAdapter(
        homeserver="https://matrix.example.com",
        user_id="@bot:example.org",
        access_token="mock",
        config_path="agent.json",
        work_dir="/tmp/matrix_test",
        token_limit=10_000,
        max_steps=10,
    )
    a._client = AsyncMock()
    a._client.user_id = "@bot:example.org"
    a._send_text = AsyncMock()
    a._client.room_send = AsyncMock()

    yield a

    GlobalOrchestrator.run = orig_run  # type: ignore[assignment]


class TestMatrixAdapter:
    """6 logic tests that don't need Matrix or real LLM."""

    async def _on_message(self, a: MatrixAdapter, body: str, sender: str = "@user:example.org") -> None:
        await a._on_message(FakeRoom(), FakeEvent(body, sender=sender))

    @pytest.mark.asyncio
    async def test_objective_runs_orchestrator(self, adapter: MatrixAdapter) -> None:
        """User message → orchestrator.run() → response sent back."""
        await self._on_message(adapter, "Write hello.py")
        calls = adapter._send_text.call_args_list
        assert len(calls) >= 2
        assert "OK:" in calls[1][0][1]

    @pytest.mark.asyncio
    async def test_question_answered_by_next_message(self, adapter: MatrixAdapter) -> None:
        """Pending question is answered by the next message in same room."""
        adapter._get_orchestrator("!test:example.org").human_channel.ask(
            project_id="!test:example.org", from_agent="coder", question="Which file?",
        )
        assert len(adapter._get_orchestrator("!test:example.org").human_channel.get_pending_questions()) == 1
        await self._on_message(adapter, "calculator.py")
        assert len(adapter._get_orchestrator("!test:example.org").human_channel.get_pending_questions()) == 0
        assert len(adapter._get_orchestrator("!test:example.org").human_channel.get_answered_questions()) == 1

    @pytest.mark.asyncio
    async def test_own_messages_ignored(self, adapter: MatrixAdapter) -> None:
        """Bot should not respond to its own messages."""
        await self._on_message(adapter, "hello", sender="@bot:example.org")
        assert not adapter._send_text.called

    @pytest.mark.asyncio
    async def test_empty_messages_ignored(self, adapter: MatrixAdapter) -> None:
        """Empty/whitespace messages should be ignored."""
        await self._on_message(adapter, "   ")
        assert not adapter._send_text.called

    @pytest.mark.asyncio
    async def test_poll_sends_to_correct_room(self, adapter: MatrixAdapter) -> None:
        """Question polling sends to the room that owns the question."""
        adapter._get_orchestrator("!other:room").human_channel.ask(
            project_id="!other:room", from_agent="reviewer", question="Edge case?",
        )
        await adapter._poll_human_questions_once()
        call = adapter._send_text.call_args_list[0]
        assert call[0][0] == "!other:room"
        assert "Edge case?" in call[0][1]

    @pytest.mark.asyncio
    async def test_no_questions_no_poll_output(self, adapter: MatrixAdapter) -> None:
        """No spurious output when there are no pending questions."""
        adapter._get_orchestrator("!test:example.org").human_channel._questions.clear()
        adapter._get_orchestrator("!other:room").human_channel._questions.clear()
        await adapter._poll_human_questions_once()
        assert not adapter._send_text.called

    def test_room_work_dir_isolation(self) -> None:
        """Different rooms get different work directories."""
        a = MatrixAdapter(
            homeserver="https://matrix.example.com",
            user_id="@bot:example.org",
            access_token="mock",
            config_path="agent.json",
            work_dir="/tmp/matrix_test",
        )
        w1 = a._room_work_dir("!room:a")
        w2 = a._room_work_dir("!room:b")
        assert w1 != w2
        assert w1.exists()
        assert w2.exists()
