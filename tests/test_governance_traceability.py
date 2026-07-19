"""Tests for source-to-claim traceability."""

from __future__ import annotations

from openagents_orchestration.governance.models import EvidenceEntry
from openagents_orchestration.governance.traceability import (
    build_source_to_claim_trace,
    traceability_gate_passed,
)


def test_traceability_links_answer_claim_to_selected_evidence() -> None:
    evidence = [
        EvidenceEntry(
            evidence_id="ev-1",
            case_id="case-1",
            run_id="run-1",
            source_ref="/docs/nas.md",
            retrieval_query="NAS access",
            summary="NAS access modes are documented.",
            sensitivity="public_safe",
            selected=True,
        )
    ]

    trace = build_source_to_claim_trace(
        {
            "closed": True,
            "answer": "Use the documented NAS access modes.",
            "actions": ["answer_user"],
        },
        evidence,
    )

    answer_claim = next(item for item in trace if item.claim_type == "answer")
    assert answer_claim.status == "supported"
    assert answer_claim.evidence_ids == ["ev-1"]
    assert answer_claim.source_refs == ["/docs/nas.md"]
    assert answer_claim.claim_id in evidence[0].metadata["supports_claims"]
    assert traceability_gate_passed(trace) is True


def test_traceability_gate_fails_closed_answer_without_evidence() -> None:
    trace = build_source_to_claim_trace(
        {
            "closed": True,
            "answer": "The request has been handled.",
            "actions": ["answer_user"],
        },
        [],
    )

    assert any(item.status == "unsupported" for item in trace)
    assert traceability_gate_passed(trace) is False
