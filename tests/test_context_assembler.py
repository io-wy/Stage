"""Tests for CompressingContextAssembler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.context import CompressingContextAssembler


def _request(session_id: str = "test-session") -> MagicMock:
    req = MagicMock()
    req.session_id = session_id
    return req


def _session_manager(messages: list[dict], artifacts: list | None = None) -> MagicMock:
    sm = MagicMock()
    sm.load_messages = AsyncMock(return_value=messages)
    sm.list_artifacts = AsyncMock(return_value=artifacts or [])
    return sm


@pytest.mark.asyncio
async def test_dedup_collapses_exact_tool_result_duplicates() -> None:
    long_output = "line\n" * 50  # > dedup_min_bytes default of 200
    messages = [
        {"role": "user", "content": "Run git status"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "bash"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": long_output},
        {"role": "assistant", "content": "Now run it again"},
        {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": long_output},
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 2000,
            "reserve_for_response": 500,
            "snip_threshold": 1.0,  # disable snip
            "dedup_threshold": 0.0,  # always dedup
            "summarize_threshold": 1.0,  # disable summarize
            "hard_collapse_threshold": 1.0,  # disable hard collapse
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    assert "dedup" in result.metadata["layers_fired"]
    assert result.metadata["deduped_messages"] == 1
    assert len(result.transcript) == 5
    assert "Duplicate bash result omitted" in result.transcript[4]["content"]


@pytest.mark.asyncio
async def test_dedup_disabled_when_config_false() -> None:
    long_output = "line\n" * 50
    messages = [
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": long_output},
        {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": long_output},
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 2000,
            "reserve_for_response": 500,
            "snip_threshold": 1.0,
            "dedup_enabled": False,
            "dedup_threshold": 0.0,
            "summarize_threshold": 1.0,
            "hard_collapse_threshold": 1.0,
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    assert "dedup" not in result.metadata["layers_fired"]
    assert result.metadata["deduped_messages"] == 0
    assert result.transcript[1]["content"] == long_output


@pytest.mark.asyncio
async def test_dedup_does_not_affect_user_or_assistant_text() -> None:
    text = "This is the same user message repeated.\n" * 20
    messages = [
        {"role": "user", "content": text},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": text},
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 2000,
            "reserve_for_response": 500,
            "snip_threshold": 1.0,
            "dedup_threshold": 0.0,
            "summarize_threshold": 1.0,
            "hard_collapse_threshold": 1.0,
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    assert result.metadata["deduped_messages"] == 0
    assert result.transcript[2]["content"] == text


@pytest.mark.asyncio
async def test_dedup_skips_short_results() -> None:
    short_output = "short output\n"
    messages = [
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": short_output},
        {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": short_output},
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 2000,
            "reserve_for_response": 500,
            "snip_threshold": 1.0,
            "dedup_threshold": 0.0,
            "dedup_min_bytes": 200,
            "summarize_threshold": 1.0,
            "hard_collapse_threshold": 1.0,
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    assert result.metadata["deduped_messages"] == 0
    assert result.transcript[1]["content"] == short_output


@pytest.mark.asyncio
async def test_dedup_respects_lookback_window() -> None:
    long_output = "line\n" * 50
    messages = [
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": long_output},
        {"role": "user", "content": "msg"},
        {"role": "user", "content": "msg"},
        {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": long_output},
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 2000,
            "reserve_for_response": 500,
            "snip_threshold": 1.0,
            "dedup_threshold": 0.0,
            "dedup_lookback": 2,
            "summarize_threshold": 1.0,
            "hard_collapse_threshold": 1.0,
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    # The duplicate is 3 messages away from the original, outside lookback=2.
    assert result.metadata["deduped_messages"] == 0
    assert result.transcript[3]["content"] == long_output


@pytest.mark.asyncio
async def test_snip_runs_before_dedup() -> None:
    """Ensure layer ordering: snip fires first when threshold is met."""
    very_long = "x" * 5000
    messages = [
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": very_long},
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 4000,
            "reserve_for_response": 500,
            "snip_threshold": 0.0,
            "dedup_threshold": 1.0,  # disable dedup
            "summarize_threshold": 1.0,
            "hard_collapse_threshold": 1.0,
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    assert result.metadata["layers_fired"] == ["snip"]
    assert "snipped" in result.transcript[0]["content"]
    assert result.metadata["deduped_messages"] == 0


@pytest.mark.asyncio
async def test_dedup_anthropic_style_tool_result_blocks() -> None:
    long_output = "line\n" * 50
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": long_output}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu2", "content": long_output}
            ],
        },
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 2000,
            "reserve_for_response": 500,
            "snip_threshold": 1.0,
            "dedup_threshold": 0.0,
            "summarize_threshold": 1.0,
            "hard_collapse_threshold": 1.0,
        }
    )
    result = await assembler.assemble(
        request=_request(),
        session_state={},
        session_manager=_session_manager(messages),
    )

    assert result.metadata["deduped_messages"] == 1
    assert "Duplicate tool result omitted" in result.transcript[1]["content"]
