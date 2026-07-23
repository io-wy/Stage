"""Evidence-only RAG answer layer tests."""

from __future__ import annotations

from openagents_orchestration.rag import (
    DocMetadata,
    EvidenceOnlyAnswerer,
    RetrievalResult,
    build_pipeline,
)
from openagents_orchestration.rag.types import Passage


def test_evidence_only_answerer_requires_citations():
    result = RetrievalResult(
        query="SAST Link 是什么？",
        passages=[
            Passage(
                text="SAST Link 是统一身份认证系统。",
                score=1.0,
                metadata=DocMetadata(source="/wiki/SAST Link.md", tags=["perm:public"]),
            )
        ],
    )

    answer = EvidenceOnlyAnswerer().answer(
        result,
        allowed_permission_tags=("perm:public",),
    )

    assert answer.status == "answered"
    assert "[1]" in answer.answer_text
    assert answer.citations[0].source == "/wiki/SAST Link.md"
    assert answer.citations[0].rank == 1


def test_evidence_only_answerer_refuses_without_evidence():
    answer = EvidenceOnlyAnswerer().answer(
        RetrievalResult(query="未知问题", passages=[]),
        allowed_permission_tags=("perm:public",),
    )

    assert answer.status == "no_evidence"
    assert answer.answer_text == ""
    assert answer.citations == ()


def test_evidence_only_answerer_refuses_disallowed_sensitive_evidence():
    result = RetrievalResult(
        query="服务器密码是什么？",
        passages=[
            Passage(
                text="敏感内容不应对公开用户展示。",
                score=1.0,
                metadata=DocMetadata(source="/wiki/secret.md", tags=["perm:sensitive"]),
            )
        ],
    )

    answer = EvidenceOnlyAnswerer().answer(
        result,
        allowed_permission_tags=("perm:public",),
    )

    assert answer.status == "refused"
    assert answer.answer_text == ""
    assert "permission" in answer.refusal_reason
    assert answer.citations == ()


async def test_pipeline_answer_with_log_adds_answer_stage():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "SAST Link 是统一身份认证系统。",
        DocMetadata(source="/wiki/SAST Link.md", tags=["SAST Link", "perm:public"]),
    )

    log = await pipe.answer_with_log(
        "SAST Link 是什么？",
        top_k=1,
        allowed_permission_tags=("perm:public", "perm:internal"),
    )

    assert log.answer is not None
    assert log.answer.status == "answered"
    assert log.answer.citations[0].source == "/wiki/SAST Link.md"
    assert log.retrieval.passages[0].source == "/wiki/SAST Link.md"


async def test_pipeline_answer_with_log_refuses_sensitive_evidence_for_public_user():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "服务器 password 内部说明。",
        DocMetadata(source="/wiki/SAST 设施指南/docx/服务器登录.md", tags=["SAST 设施指南"]),
    )

    log = await pipe.answer_with_log(
        "服务器内部说明是什么？",
        top_k=1,
        allowed_permission_tags=("perm:public",),
        use_route_classifier=False,
    )

    assert log.retrieval.passages
    assert "perm:sensitive" in log.retrieval.passages[0].tags
    assert log.answer is not None
    assert log.answer.status == "refused"
    assert log.answer.citations == ()
