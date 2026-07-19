"""Tests for Stage governance evidence adapters."""

from __future__ import annotations

from openagents_orchestration.governance.evidence import (
    build_public_evidence_summary,
    evidence_entries_from_rag_log,
)
from openagents_orchestration.rag.runlog import (
    RagQueryRunLog,
    RagRetrievalLog,
    RagRetrievalPassageLog,
)


def _log(passages: list[RagRetrievalPassageLog]) -> RagQueryRunLog:
    return RagQueryRunLog(
        query="NAS",
        retrieval=RagRetrievalLog(
            query="NAS",
            top_k=2,
            passages=passages,
        ),
    )


def test_evidence_entries_from_rag_log_preserve_sources() -> None:
    log = _log(
        [
            RagRetrievalPassageLog(
                rank=1,
                source="/docs/nas.md",
                score=0.99,
                snippet="NAS registration uses Feishu form.",
            ),
            RagRetrievalPassageLog(
                rank=2,
                source="/docs/other.md",
                score=0.8,
                snippet="Fallback account removed.",
            ),
        ]
    )

    entries = evidence_entries_from_rag_log(log, case_id="case-1", run_id="run-1")

    assert [entry.source_ref for entry in entries] == ["/docs/nas.md", "/docs/other.md"]
    assert entries[0].sensitivity == "public_safe"
    assert entries[1].summary


def test_public_evidence_summary_redacts_sensitive_passages() -> None:
    log = _log(
        [
            RagRetrievalPassageLog(
                rank=1,
                source="/docs/nas.md",
                score=0.99,
                snippet="Deprecated shared account should not be reused.",
            )
        ]
    )

    entries = evidence_entries_from_rag_log(log, case_id="case-1", run_id="run-1")
    summary = build_public_evidence_summary(entries)

    assert summary[0]["sensitivity"] == "secret_risk"
    assert summary[0]["summary"] == "[redacted]"
