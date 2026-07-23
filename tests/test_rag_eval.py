"""RAG retrieval 过程评测测试。"""

from __future__ import annotations

from openagents_orchestration.rag import (
    DocMetadata,
    RetrievalEvalCase,
    RetrievalEvalItem,
    RetrievalTracePassage,
    build_pipeline,
    evaluate_retrieval,
)


async def test_evaluate_retrieval_hit_rate_and_mrr():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "SAST Link 是统一身份认证系统",
        DocMetadata(source="SAST Link/docx/SAST Link 食用指南.docx", tags=["link"]),
    )
    await pipe.ingest_text(
        "SAST 成员守则要求不得泄露内部机密信息",
        DocMetadata(source="SAST 规章制度/docx/SAST 成员守则.docx", tags=["rules"]),
    )

    summary = await evaluate_retrieval(
        pipe,
        [
            RetrievalEvalCase(
                question="SAST Link 是什么",
                expected_sources=("SAST Link 食用指南",),
                top_k=2,
                query_type="项目说明",
                route="SAST Link",
            ),
            RetrievalEvalCase(
                question="成员守则有什么要求",
                expected_sources=("SAST 成员守则",),
                top_k=2,
                query_type="规则制度",
                route="SAST 规章制度",
            ),
        ],
    )

    assert summary.total == 2
    assert summary.hits == 2
    assert summary.hit_rate == 1.0
    assert summary.top1_accuracy == 1.0
    assert summary.mrr == 1.0
    assert summary.avg_hit_rank == 1.0
    assert summary.miss_rate == 0.0
    assert summary.precision_at_k == 0.5
    assert summary.recall_at_k == 1.0
    assert summary.ndcg_at_k == 1.0
    assert summary.avg_unique_sources == 2.0
    assert summary.avg_score_gap_to_hit == 0.0
    assert summary.hit_rate_by_query_type() == {"项目说明": 1.0, "规则制度": 1.0}
    assert summary.hit_rate_by_route() == {"SAST Link": 1.0, "SAST 规章制度": 1.0}
    assert summary.hit_rate_by_failure_cause() == {"retrieval": 1.0}
    assert summary.breakdown_by_query_type()["项目说明"]["precision_at_k"] == 0.5
    assert summary.breakdown_by_query_type()["项目说明"]["recall_at_k"] == 1.0
    assert summary.breakdown_by_route()["SAST Link"]["top1_accuracy"] == 1.0
    assert summary.breakdown_by_failure_cause()["retrieval"]["hits"] == 2
    assert summary.items[0].trace[0].rank == 1
    assert summary.items[0].trace[0].source.endswith("SAST Link 食用指南.docx")
    assert "统一身份认证" in summary.items[0].trace[0].snippet
    assert summary.items[0].matched_expected_source == "SAST Link 食用指南"
    assert summary.items[0].recalled_expected_sources == ("SAST Link 食用指南",)
    assert summary.items[0].precision_at_k == 0.5
    assert summary.items[0].recall_at_k == 1.0
    assert summary.items[0].ndcg_at_k == 1.0
    as_dict = summary.to_dict()
    assert as_dict["metrics"]["hit_rate"] == 1.0
    assert as_dict["items"][0]["matched_source"].endswith("SAST Link 食用指南.docx")
    assert as_dict["items"][0]["trace"][0]["rank"] == 1


async def test_evaluate_retrieval_miss():
    pipe = build_pipeline()
    await pipe.ingest_text("烘焙 蛋糕 食谱", DocMetadata(source="food"))

    summary = await evaluate_retrieval(
        pipe,
        [
            RetrievalEvalCase(
                question="机器学习模型",
                expected_sources=("ml",),
                top_k=1,
                failure_cause="knowledge",
            )
        ],
    )

    assert summary.hits == 0
    assert summary.hit_rate == 0.0
    assert summary.miss_rate == 1.0
    assert summary.mrr == 0.0
    assert summary.precision_at_k == 0.0
    assert summary.recall_at_k == 0.0
    assert summary.ndcg_at_k == 0.0
    assert summary.misses_by_failure_cause() == {"knowledge": 1}
    assert summary.misses_by_route() == {"general": 1}
    assert summary.misses_by_query_type() == {"general": 1}
    assert summary.breakdown_by_failure_cause()["knowledge"]["misses"] == 1


async def test_evaluate_retrieval_applies_required_tags():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "服务器公开说明",
        DocMetadata(source="public-server", tags=["facilities", "perm:public"]),
    )
    await pipe.ingest_text(
        "服务器 password 内部说明",
        DocMetadata(source="sensitive-server", tags=["facilities", "perm:sensitive"]),
    )

    summary = await evaluate_retrieval(
        pipe,
        [
            RetrievalEvalCase(
                question="服务器说明",
                expected_sources=("sensitive-server",),
                top_k=1,
                filter_tags=("facilities",),
                required_tags=("perm:sensitive",),
            )
        ],
    )

    assert summary.hit_rate == 1.0
    assert summary.items[0].trace[0].source == "sensitive-server"


def test_retrieval_item_recall_counts_expected_source_coverage():
    item = RetrievalEvalItem(
        case=RetrievalEvalCase(
            question="部门有哪些资料",
            expected_sources=("部门介绍", "欢迎来到校科协", "办公室 介绍"),
        ),
        trace=(
            RetrievalTracePassage(
                rank=1,
                source="/wiki/部门介绍.md",
                score=1.0,
                snippet="部门介绍",
            ),
            RetrievalTracePassage(
                rank=2,
                source="/wiki/欢迎来到校科协.md",
                score=0.9,
                snippet="欢迎来到校科协",
            ),
        ),
        hit_rank=1,
        matched_source="/wiki/部门介绍.md",
        matched_expected_source="部门介绍",
    )

    assert item.recalled_expected_sources == ("部门介绍", "欢迎来到校科协")
    assert item.recall_at_k == 2 / 3
    assert item.precision_at_k == 1.0
    assert item.to_dict()["recall_at_k"] == 2 / 3
