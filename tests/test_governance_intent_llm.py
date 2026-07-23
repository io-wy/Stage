"""Tests for governance intent LLM wiring."""

from __future__ import annotations

import pytest

from openagents_orchestration.control import intent_llm
from openagents_orchestration.intent_classifier import IntentClassifier


def test_governance_intent_llm_auto_mode_returns_none_without_key(monkeypatch) -> None:
    monkeypatch.delenv("STAGE_INTENT_LLM_ENABLED", raising=False)
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:9999")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    assert intent_llm.create_governance_intent_llm_client() is None


def test_governance_intent_llm_force_enabled_requires_config(monkeypatch) -> None:
    monkeypatch.setenv("STAGE_INTENT_LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:9999")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Stage intent LLM is enabled"):
        intent_llm.create_governance_intent_llm_client()


def test_governance_intent_classifier_uses_created_llm(monkeypatch) -> None:
    created = object()

    def fake_create(options):
        assert options.provider == "mock"
        assert options.model == "intent-test-model"
        return created

    monkeypatch.setenv("STAGE_INTENT_LLM_ENABLED", "true")
    monkeypatch.setenv("STAGE_INTENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("STAGE_INTENT_LLM_MODEL", "intent-test-model")
    monkeypatch.setattr(intent_llm, "create_llm_client", fake_create)

    classifier = intent_llm.build_governance_intent_classifier()

    assert isinstance(classifier, IntentClassifier)
    assert classifier._llm is created
