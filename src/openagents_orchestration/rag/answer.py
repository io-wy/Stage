"""Evidence-only answer layer.

This is intentionally not an LLM generator. It formats retrieved passages into
a cited answer, so the first answer stage is faithful, auditable, and permission
aware.
"""

from __future__ import annotations

from openagents_orchestration.rag.runlog import RagAnswerCitationLog, RagAnswerLog
from openagents_orchestration.rag.types import Passage, RetrievalResult

_PERMISSION_TAGS = {"perm:public", "perm:internal", "perm:sensitive"}
_DEFAULT_ALLOWED_PERMISSION_TAGS = ("perm:public", "perm:internal")


class EvidenceOnlyAnswerer:
    """Build an extractive answer strictly from retrieved passages."""

    def answer(
        self,
        result: RetrievalResult,
        allowed_permission_tags: tuple[str, ...] | list[str] | None = None,
        max_passages: int = 3,
    ) -> RagAnswerLog:
        passages = result.passages[:max_passages]
        if not passages:
            return RagAnswerLog(
                status="no_evidence",
                refusal_reason="no retrieval evidence available",
            )

        allowed = set(allowed_permission_tags or _DEFAULT_ALLOWED_PERMISSION_TAGS)
        disallowed = [
            passage
            for passage in passages
            if not _passage_permissions(passage) <= allowed
        ]
        if disallowed:
            return RagAnswerLog(
                status="refused",
                refusal_reason="permission denied for retrieved evidence",
            )

        citations = tuple(
            RagAnswerCitationLog(
                source=passage.metadata.source,
                rank=idx,
                snippet=_snippet(passage.text),
            )
            for idx, passage in enumerate(passages, start=1)
        )
        answer_text = "\n".join(
            f"[{citation.rank}] {citation.snippet}" for citation in citations
        )
        return RagAnswerLog(
            status="answered",
            answer_text=answer_text,
            citations=citations,
        )


def _passage_permissions(passage: Passage) -> set[str]:
    permissions = set(passage.metadata.tags) & _PERMISSION_TAGS
    return permissions or {"perm:internal"}


def _snippet(text: str, max_chars: int = 220) -> str:
    return " ".join(text.split())[:max_chars]
