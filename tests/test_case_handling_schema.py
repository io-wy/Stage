"""Tests for the case-handling benchmark schema."""

from __future__ import annotations

import pytest

from eval.case_handling.schema import CaseSpec


def test_case_spec_accepts_valid_case() -> None:
    case = CaseSpec(
        case_id="faq-001",
        family="knowledge",
        source="SAST 设施指南",
        objective="Answer a question about NAS access.",
        context_pack={"sources": ["NAS.md"], "facts": ["NAS is shared storage"]},
        success_criteria=[
            "Answer identifies the correct NAS path.",
            "Answer cites the relevant wiki source.",
        ],
        verifier={"type": "manual"},
        difficulty="easy",
    )

    assert case.case_id == "faq-001"
    assert case.family == "knowledge"
    assert case.difficulty == "easy"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (
            {
                "case_id": "",
                "family": "knowledge",
                "source": "SAST 设施指南",
                "objective": "Answer a question.",
                "context_pack": {"sources": []},
                "success_criteria": ["Must answer the question."],
                "verifier": {"type": "manual"},
                "difficulty": "easy",
            },
            "case_id",
        ),
        (
            {
                "case_id": "faq-002",
                "family": "knowledge",
                "source": "SAST 设施指南",
                "objective": "Answer a question.",
                "context_pack": {"sources": []},
                "success_criteria": [],
                "verifier": {"type": "manual"},
                "difficulty": "easy",
            },
            "success criteria",
        ),
        (
            {
                "case_id": "faq-003",
                "family": "knowledge",
                "source": "SAST 设施指南",
                "objective": "Answer a question.",
                "context_pack": {"sources": []},
                "success_criteria": ["尽量回答得准确。"],
                "verifier": {"type": "manual"},
                "difficulty": "easy",
            },
            "ambiguous",
        ),
        (
            {
                "case_id": "faq-004",
                "family": "knowledge",
                "source": "SAST 设施指南",
                "objective": "Answer a question.",
                "context_pack": {"sources": []},
                "success_criteria": ["Must answer the question."],
                "verifier": {"type": "manual"},
                "difficulty": "extreme",
            },
            "difficulty",
        ),
    ],
)
def test_case_spec_rejects_invalid_cases(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        CaseSpec(**kwargs)  # type: ignore[arg-type]

