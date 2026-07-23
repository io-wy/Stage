"""Tests for IntentClassifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents_orchestration.intent_classifier import (
    IntentClassifier,
    IntentResult,
    IntentSchema,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_llm(return_schema: IntentSchema) -> AsyncMock:
    """Return a mock LLM client that yields a given IntentSchema."""
    mock = AsyncMock()
    # structured_generate calls llm_client.generate(), not mock.generate()
    mock.generate = AsyncMock(return_value=MagicMock(
        output_text=return_schema.model_dump_json(),
        usage=MagicMock(total_tokens=42),
    ))
    return mock


# ── L0 cache hit ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l0_cache_hit():
    """Second call with same objective returns cached result, no LLM call."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(
        return_value=MagicMock(
            output_text=IntentSchema(
                task_type="feature",
                complexity="simple",
                confidence=0.9,
                reason="test",
            ).model_dump_json(),
            usage=MagicMock(total_tokens=10),
        )
    )

    classifier = IntentClassifier(llm_client=mock_llm)

    obj = "Some obscure task xyz123"
    result1 = await classifier.classify(obj)
    result2 = await classifier.classify(obj)

    assert result1.task_type == "feature"
    assert result2.task_type == "feature"
    # L0 cache means only one LLM call
    mock_llm.generate.assert_awaited_once()
    assert result1.source == "L3_llm"
    assert result2.source == "L3_llm"


# ── L1 keyword rules ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l1_keyword_rules():
    """Keyword matching returns correct task_type without LLM call."""
    mock_llm = AsyncMock()
    classifier = IntentClassifier(llm_client=mock_llm)

    test_cases = [
        ("write tests for login module", "test"),
        ("fix the broken authentication", "bug_fix"),
        ("refactor the user service", "refactor"),
        ("create a new API endpoint", "feature"),
        ("implement a function to hash passwords", "feature"),
        ("review this pull request", "review"),
        ("write README documentation", "doc"),
    ]

    for objective, expected_type in test_cases:
        result = await classifier.classify(objective)
        assert result.task_type == expected_type, f"Failed for: {objective}"
        assert result.source == "L1_rule"
        assert result.confidence > 0

    # L1 complexity expectations (prevent hard-coded "complex" for broad keywords)
    assert (await classifier.classify("create a new API endpoint")).complexity == "medium"
    assert (await classifier.classify("implement a function to hash passwords")).complexity == "simple"

    # No LLM calls for keyword matches
    mock_llm.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_l1_keyword_rules_do_not_match_substrings():
    classifier = IntentClassifier(llm_client=None)

    verification = await classifier.classify("send a verification SMS")
    configuration = await classifier.classify("change NUT configuration")

    assert verification.task_type == "unknown"
    assert configuration.task_type == "unknown"


# ── L3 LLM call ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l3_llm_call():
    """No cache, no keyword match → LLM is called and result is parsed."""
    schema = IntentSchema(
        task_type="shell",
        complexity="simple",
        external=["docker"],
        priority="urgent",
        confidence=0.92,
        reason="System administration task",
    )
    mock_llm = _make_mock_llm(schema)
    classifier = IntentClassifier(llm_client=mock_llm)

    obj = "Some obscure task abc789"
    result = await classifier.classify(obj)

    assert result.task_type == "shell"
    assert result.complexity == "simple"
    assert result.external == ["docker"]
    assert result.priority == "urgent"
    assert result.confidence == pytest.approx(0.92)
    assert result.reason == "System administration task"
    assert result.source == "L3_llm"

    mock_llm.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_frame_async_uses_llm_for_semantic_service_request():
    schema = IntentSchema(
        task_type="shell",
        complexity="complex",
        external=["lark"],
        priority="normal",
        confidence=0.88,
        reason="用户请求执行权限类操作，需要治理层继续判断。",
    )
    mock_llm = _make_mock_llm(schema)
    classifier = IntentClassifier(llm_client=mock_llm)

    frame = await classifier.classify_frame_async("帮我开一下文档编辑权限，审批稍后补。")

    assert frame.task_type == "shell"
    assert frame.complexity == "complex"
    assert frame.external == ["lark"]
    assert frame.source == "L3_llm"
    assert frame.reason == "用户请求执行权限类操作，需要治理层继续判断。"
    mock_llm.generate.assert_awaited_once()


# ── L4 feedback overrides cache ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l4_feedback_overrides_cache():
    """feedback() overwrites cached result; subsequent classify returns new value."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(
        return_value=MagicMock(
            output_text=IntentSchema(
                task_type="feature",
                complexity="medium",
                confidence=0.75,
                reason="initial",
            ).model_dump_json(),
            usage=MagicMock(total_tokens=10),
        )
    )

    classifier = IntentClassifier(llm_client=mock_llm)
    obj = "Build a payment gateway"

    # First classify → LLM
    result1 = await classifier.classify(obj)
    assert result1.task_type == "feature"

    # Feedback override
    corrected = IntentResult(
        task_type="refactor",
        complexity="complex",
        confidence=0.95,
        reason="User correction",
        source="L4_feedback",
    )
    classifier.feedback(obj, corrected)

    # Second classify → should use feedback, not cache or LLM
    result2 = await classifier.classify(obj)
    assert result2.task_type == "refactor"
    assert result2.source == "L4_feedback"
    assert result2.confidence == pytest.approx(0.95)

    # Only one LLM call total
    mock_llm.generate.assert_awaited_once()


# ── LLM failure fallback ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_failure_fallback():
    """When LLM raises, classify returns a controlled governance fallback."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    classifier = IntentClassifier(llm_client=mock_llm)

    obj = "Some obscure task that needs LLM"
    result = await classifier.classify(obj)

    assert result.task_type == "unknown"
    assert result.complexity == "medium"
    assert result.confidence == 0.0
    assert result.source == "intent_llm_error"
    assert result.reason == "语义模型暂不可用，已使用确定性兜底"


# ── confidence clamping ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confidence_clamped():
    """Confidence values outside [0,1] are rejected by Pydantic validation."""
    # IntentSchema has ge=0.0, le=1.0 on confidence field
    with pytest.raises(ValueError):
        IntentSchema(
            task_type="feature",
            complexity="simple",
            confidence=1.5,  # > 1.0
            reason="too high",
        )

    with pytest.raises(ValueError):
        IntentSchema(
            task_type="feature",
            complexity="simple",
            confidence=-0.3,  # < 0.0
            reason="too low",
        )

    # Valid boundaries
    schema_low = IntentSchema(
        task_type="feature",
        complexity="simple",
        confidence=0.0,
        reason="min",
    )
    assert schema_low.confidence == 0.0

    schema_high = IntentSchema(
        task_type="feature",
        complexity="simple",
        confidence=1.0,
        reason="max",
    )
    assert schema_high.confidence == 1.0


# ── no LLM client fallback ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_llm_client_fallback():
    """When llm_client is None and no keyword matches, returns unknown fallback."""
    classifier = IntentClassifier(llm_client=None)
    obj = "Some obscure task with no keywords"
    result = await classifier.classify(obj)

    assert result.task_type == "unknown"
    assert result.complexity == "medium"
    assert result.confidence == 0.0
    assert result.source == "fallback"
    assert result.reason == "规则未命中，已使用确定性兜底"


# ── cache stores L1 results ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l1_result_is_cached():
    """Keyword rule results are cached so second call skips even rule matching."""
    mock_llm = AsyncMock()
    classifier = IntentClassifier(llm_client=mock_llm)

    obj = "fix the authentication bug"
    result1 = await classifier.classify(obj)
    assert result1.source == "L1_rule"

    # Patch _match_rules to detect if it's called again
    original_match = classifier._match_rules
    call_count = 0

    def counting_match(o: str):
        nonlocal call_count
        call_count += 1
        return original_match(o)

    classifier._match_rules = counting_match  # type: ignore[method-assign]

    result2 = await classifier.classify(obj)
    assert result2.source == "L1_rule"
    # L0 cache hit means _match_rules is NOT called again
    assert call_count == 0


# ── IntentResult.from_schema ──────────────────────────────────────────────────


def test_intent_result_from_schema():
    """IntentResult.from_schema copies all fields correctly."""
    schema = IntentSchema(
        task_type="test",
        complexity="medium",
        external=["pytest"],
        priority="normal",
        confidence=0.85,
        reason="Keyword match",
    )
    result = IntentResult.from_schema(schema, source="L3_llm")

    assert result.task_type == "test"
    assert result.complexity == "medium"
    assert result.external == ["pytest"]
    assert result.priority == "normal"
    assert result.confidence == 0.85
    assert result.reason == "Keyword match"
    assert result.source == "L3_llm"


def test_intent_result_to_dict():
    """to_dict produces expected keys and values."""
    result = IntentResult(
        task_type="doc",
        complexity="simple",
        external=[],
        priority="low",
        confidence=0.7,
        reason="docs",
        source="L1_rule",
    )
    d = result.to_dict()
    assert d == {
        "task_type": "doc",
        "complexity": "simple",
        "external": [],
        "priority": "low",
        "confidence": 0.7,
        "reason": "docs",
        "source": "L1_rule",
    }
