"""LLM wiring for Stage governance intent classification."""

from __future__ import annotations

import os
from typing import Any

from openagents.config.schema import LLMOptions
from openagents.llm.registry import create_llm_client

from openagents_orchestration.intent_classifier import IntentClassifier

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def build_governance_intent_classifier() -> IntentClassifier:
    """Build the product intent classifier with optional semantic LLM support."""
    return IntentClassifier(llm_client=create_governance_intent_llm_client())


def create_governance_intent_llm_client() -> Any | None:
    """Create an env-configured LLM client for intent classification.

    The default mode is ``auto``: use the LLM when provider/base/model/key are
    configured, otherwise return ``None`` so the classifier can use deterministic
    governance fallback. Set ``STAGE_INTENT_LLM_ENABLED=false`` to force-disable
    it, or ``true`` to require creation and surface bad configuration.
    """
    enabled = os.environ.get("STAGE_INTENT_LLM_ENABLED", "auto").strip().lower()
    if enabled in _FALSE_VALUES:
        return None

    provider = _env("STAGE_INTENT_LLM_PROVIDER", "LLM_PROVIDER") or "openai_compatible"
    model = _env("STAGE_INTENT_LLM_MODEL", "LLM_MODEL")
    api_base = _env("STAGE_INTENT_LLM_API_BASE", "LLM_API_BASE")
    api_key_env = (
        _env("STAGE_INTENT_LLM_API_KEY_ENV", "LLM_API_KEY_ENV") or "LLM_API_KEY"
    )
    timeout_ms = _env_int("STAGE_INTENT_LLM_TIMEOUT_MS", default=30_000)
    force_enabled = enabled in _TRUE_VALUES

    if not _has_required_llm_config(
        provider=provider,
        model=model,
        api_base=api_base,
        api_key_env=api_key_env,
    ):
        if force_enabled:
            raise RuntimeError(
                "Stage intent LLM is enabled but LLM_API_BASE, LLM_MODEL, or "
                f"{api_key_env} is missing."
            )
        return None

    options = LLMOptions(
        provider=provider,
        model=model,
        api_base=api_base,
        api_key_env=api_key_env,
        temperature=0.0,
        max_tokens=512,
        timeout_ms=timeout_ms,
    )
    try:
        return create_llm_client(options)
    except Exception:
        if force_enabled:
            raise
        return None


def _has_required_llm_config(
    *,
    provider: str,
    model: str | None,
    api_base: str | None,
    api_key_env: str,
) -> bool:
    if provider == "mock":
        return True
    return bool(model and api_base and os.environ.get(api_key_env))


def _env(primary: str, fallback: str) -> str | None:
    value = os.environ.get(primary)
    if value is None or not value.strip():
        value = os.environ.get(fallback)
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_int(name: str, *, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
