from __future__ import annotations

from openagents_orchestration.utils.runtime_compat import apply_sdk_patches


def test_apply_sdk_patches_allows_null_tool_calls() -> None:
    from openagents.llm.providers import openai_compatible as provider

    apply_sdk_patches()

    assert provider._parse_tool_calls(None) == []
