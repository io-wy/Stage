"""Tests for ReadSkillTool — L2 progressive disclosure (full SKILL.md fetch)."""

from __future__ import annotations

import pytest
from openagents.errors.exceptions import PermanentToolError, ToolError

from openagents_orchestration.tools.read_skill import ReadSkillTool


async def test_read_skill_returns_full_doc():
    tool = ReadSkillTool()
    result = await tool.invoke({"skill_name": "adversarial-review"}, context=None)
    assert result["skill_name"] == "adversarial-review"
    assert "Adversarial Review" in result["doc"]
    assert len(result["doc"]) > 50  # full doc, not a stub


async def test_read_skill_missing_name_raises():
    tool = ReadSkillTool()
    with pytest.raises(PermanentToolError):
        await tool.invoke({"skill_name": "  "}, context=None)


async def test_read_skill_not_found_lists_available():
    tool = ReadSkillTool()
    with pytest.raises(ToolError) as exc:
        await tool.invoke({"skill_name": "no-such-skill"}, context=None)
    assert "not found" in str(exc.value).lower()


def test_schema_requires_skill_name():
    schema = ReadSkillTool().schema()
    assert schema["required"] == ["skill_name"]
    assert "skill_name" in schema["properties"]
