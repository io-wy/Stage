"""Typed schema for the case-handling benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
_AMBIGUOUS_MARKERS = (
    "尽量",
    "适当",
    "视情况",
    "必要时",
    "可能",
    "等等",
    "as needed",
    "if possible",
    "maybe",
    "roughly",
    "somewhat",
    "etc",
    "...",
)


def _clean_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    return cleaned


def _has_ambiguity(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _AMBIGUOUS_MARKERS)


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """Normalized definition of a benchmark case."""

    case_id: str
    family: str
    source: str
    objective: str
    context_pack: dict[str, Any]
    success_criteria: list[str]
    verifier: dict[str, Any]
    difficulty: str
    tool_limits: list[str] = field(default_factory=list)
    expected_human_touches: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _clean_text(self.case_id, "case_id"))
        object.__setattr__(self, "family", _clean_text(self.family, "family"))
        object.__setattr__(self, "source", _clean_text(self.source, "source"))
        object.__setattr__(self, "objective", _clean_text(self.objective, "objective"))

        if self.difficulty not in _ALLOWED_DIFFICULTIES:
            raise ValueError(
                f"difficulty must be one of {sorted(_ALLOWED_DIFFICULTIES)}"
            )

        if not isinstance(self.context_pack, dict):
            raise ValueError("context_pack must be a dictionary")
        if not isinstance(self.verifier, dict) or not self.verifier:
            raise ValueError("verifier must be a non-empty dictionary")

        if not isinstance(self.success_criteria, list) or not self.success_criteria:
            raise ValueError("success criteria must be a non-empty list")

        normalized_criteria: list[str] = []
        for idx, item in enumerate(self.success_criteria):
            criterion = _clean_text(item, f"success_criteria[{idx}]")
            if _has_ambiguity(criterion):
                raise ValueError(f"ambiguous success criterion: {criterion}")
            normalized_criteria.append(criterion)
        object.__setattr__(self, "success_criteria", normalized_criteria)

        if not isinstance(self.tool_limits, list):
            raise ValueError("tool_limits must be a list")
        normalized_tools: list[str] = []
        for idx, item in enumerate(self.tool_limits):
            normalized_tools.append(_clean_text(item, f"tool_limits[{idx}]"))
        object.__setattr__(self, "tool_limits", normalized_tools)

        if self.expected_human_touches is not None and self.expected_human_touches < 0:
            raise ValueError("expected_human_touches cannot be negative")

