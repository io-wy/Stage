"""Source-to-claim traceability for governed outputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from openagents_orchestration.control.models import EvidenceEntry

ClaimTraceStatus = Literal["supported", "unsupported", "not_required"]

_READ_ONLY_ACTIONS = {"answer_user", "ask_human", "request_human", "create_handoff"}


@dataclass(frozen=True, slots=True)
class ClaimTraceEntry:
    claim_id: str
    claim_type: str
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    status: ClaimTraceStatus = "unsupported"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "source_refs": list(self.source_refs),
            "status": self.status,
            "reason": self.reason,
        }


def build_source_to_claim_trace(
    case_result: dict[str, Any],
    evidence_entries: list[EvidenceEntry],
) -> list[ClaimTraceEntry]:
    """Build a conservative trace from public result claims to selected evidence."""

    selected_evidence = [
        entry
        for entry in evidence_entries
        if entry.selected and entry.sensitivity == "public_safe"
    ]
    evidence_ids = [entry.evidence_id for entry in selected_evidence]
    source_refs = [entry.source_ref for entry in selected_evidence if entry.source_ref]

    claims: list[ClaimTraceEntry] = []
    answer = str(case_result.get("answer", "")).strip()
    if answer:
        claims.append(
            _claim(
                claim_type="answer",
                text=answer,
                evidence_ids=evidence_ids,
                source_refs=source_refs,
                evidence_required=case_result.get("closed") is True,
            )
        )

    for action in case_result.get("actions", []):
        action_text = str(action).strip()
        if not action_text:
            continue
        evidence_required = action_text.lower() not in _READ_ONLY_ACTIONS
        claims.append(
            _claim(
                claim_type="action",
                text=action_text,
                evidence_ids=evidence_ids,
                source_refs=source_refs,
                evidence_required=evidence_required,
            )
        )

    closure_text = "closed=true" if case_result.get("closed") is True else "closed=false"
    claims.append(
        _claim(
            claim_type="closure",
            text=closure_text,
            evidence_ids=evidence_ids,
            source_refs=source_refs,
            evidence_required=case_result.get("closed") is True,
        )
    )

    _attach_claims_to_evidence(claims, selected_evidence)
    return claims


def traceability_gate_passed(trace: list[ClaimTraceEntry]) -> bool:
    return all(entry.status != "unsupported" for entry in trace)


def _claim(
    *,
    claim_type: str,
    text: str,
    evidence_ids: list[str],
    source_refs: list[str],
    evidence_required: bool,
) -> ClaimTraceEntry:
    if not evidence_required:
        status: ClaimTraceStatus = "not_required"
        reason = "claim does not require source evidence"
    elif evidence_ids:
        status: ClaimTraceStatus = "supported"
        reason = "selected evidence supports this claim"
    else:
        status = "unsupported"
        reason = "claim requires selected evidence but none is attached"
    return ClaimTraceEntry(
        claim_id=_claim_id(claim_type, text),
        claim_type=claim_type,
        text=text,
        evidence_ids=list(evidence_ids),
        source_refs=list(source_refs),
        status=status,
        reason=reason,
    )


def _claim_id(claim_type: str, text: str) -> str:
    digest = hashlib.sha1(f"{claim_type}:{text}".encode()).hexdigest()[:12]
    return f"claim-{digest}"


def _attach_claims_to_evidence(
    claims: list[ClaimTraceEntry],
    evidence_entries: list[EvidenceEntry],
) -> None:
    for entry in evidence_entries:
        supported = [
            claim.claim_id
            for claim in claims
            if entry.evidence_id in claim.evidence_ids and claim.status == "supported"
        ]
        if supported:
            entry.metadata["supports_claims"] = supported
