"""Adapters from RAG logs to governance evidence records."""

from __future__ import annotations

from typing import Any

from openagents_orchestration.governance.models import EvidenceEntry
from openagents_orchestration.governance.safety import scan_public_output
from openagents_orchestration.rag.runlog import RagQueryRunLog, RagRetrievalPassageLog


def _classify_sensitivity(passage: RagRetrievalPassageLog) -> str:
    scan_result = scan_public_output(
        {
            "source": passage.source,
            "snippet": passage.snippet,
            "score_breakdown": passage.score_breakdown,
        }
    )
    return "secret_risk" if scan_result.blocked else "public_safe"


def evidence_entries_from_rag_log(
    log: RagQueryRunLog,
    *,
    case_id: str,
    run_id: str,
) -> list[EvidenceEntry]:
    entries: list[EvidenceEntry] = []
    for passage in log.retrieval.passages:
        sensitivity = _classify_sensitivity(passage)
        summary = passage.snippet.strip() or f"passage from {passage.source}"
        if sensitivity == "secret_risk":
            summary = "[redacted]"
        entries.append(
            EvidenceEntry(
                case_id=case_id,
                run_id=run_id,
                source_ref=passage.source,
                retrieval_query=log.query,
                summary=summary,
                sensitivity=sensitivity,
                used_by=["rag_retrieval"],
                selected=True,
                metadata={
                    "rank": passage.rank,
                    "score": passage.score,
                    "tags": list(passage.tags),
                },
            )
        )
    return entries


def build_public_evidence_summary(entries: list[EvidenceEntry]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in entries:
        summary.append(
            {
                "evidence_id": entry.evidence_id,
                "source_ref": entry.source_ref,
                "sensitivity": entry.sensitivity,
                "summary": entry.summary if entry.sensitivity == "public_safe" else "[redacted]",
                "supports_claims": list(entry.metadata.get("supports_claims", [])),
                "selected": entry.selected,
                "relevance": dict(entry.metadata.get("relevance", {})),
            }
        )
    return summary
