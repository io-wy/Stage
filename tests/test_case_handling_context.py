"""Tests for the case-handling context pack builder."""

from __future__ import annotations

from eval.case_handling.context import build_context_pack, render_context_prompt
from eval.case_handling.schema import CaseSpec


def test_context_pack_is_deterministic() -> None:
    case_a = CaseSpec(
        case_id="incident-001",
        family="incident",
        source="SAST 设施指南",
        objective="Explain how to recover NAS access.",
        context_pack={
            "facts": ["NAS shares storage across teams"],
            "source_refs": ["NAS.md", "DDNS.md"],
            "notes": ["Keep answer concise."],
        },
        success_criteria=[
            "Answer names the correct NAS recovery steps.",
            "Answer cites the relevant wiki references.",
        ],
        verifier={"type": "manual"},
        difficulty="medium",
        tool_limits=["read_file"],
    )
    case_b = CaseSpec(
        case_id="incident-001",
        family="incident",
        source="SAST 设施指南",
        objective="Explain how to recover NAS access.",
        context_pack={
            "notes": ["Keep answer concise."],
            "source_refs": ["NAS.md", "DDNS.md"],
            "facts": ["NAS shares storage across teams"],
        },
        success_criteria=[
            "Answer names the correct NAS recovery steps.",
            "Answer cites the relevant wiki references.",
        ],
        verifier={"type": "manual"},
        difficulty="medium",
        tool_limits=["read_file"],
    )

    pack_a = build_context_pack(case_a)
    pack_b = build_context_pack(case_b)

    assert pack_a == pack_b
    assert pack_a["source_refs"] == ["NAS.md", "DDNS.md"]
    assert "Explain how to recover NAS access." in pack_a["prompt"]
    assert "NAS shares storage across teams" in pack_a["prompt"]
    assert "Keep answer concise." in pack_a["prompt"]


def test_render_context_prompt_is_stable() -> None:
    case = CaseSpec(
        case_id="approval-001",
        family="approval",
        source="SAST Approve",
        objective="Decide whether to approve a request.",
        context_pack={"source_refs": ["README.md"], "facts": ["Request is complete"]},
        success_criteria=["Approve only if all required fields are present."],
        verifier={"type": "manual"},
        difficulty="easy",
    )

    prompt_1 = render_context_prompt(case)
    prompt_2 = render_context_prompt(case)

    assert prompt_1 == prompt_2
    assert "case_id: approval-001" in prompt_1
    assert "success criteria" in prompt_1.lower()
    assert "README.md" in prompt_1

