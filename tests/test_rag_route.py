"""RAG route classifier 和运行日志测试。"""

from __future__ import annotations

from openagents_orchestration.rag import (
    DocMetadata,
    RagAnswerCitationLog,
    RagAnswerLog,
    RagQueryRunLog,
    RagRetrievalLog,
    RouteClassifier,
    build_pipeline,
)


def test_route_classifier_routes_facilities_question():
    decision = RouteClassifier().classify("SAST 有几台服务器？")

    assert decision.selected_route == "SAST 设施指南"
    assert decision.filter_tags == ["SAST 设施指南"]
    assert decision.confidence > 0
    assert "服务器" in decision.matched_aliases


def test_route_classifier_routes_department_question():
    decision = RouteClassifier().classify("软件研发部有哪些组？")

    assert decision.selected_route == "SAST 说明书 Public 版"
    assert decision.filter_tags == ["SAST 说明书 Public 版"]


def test_route_classifier_falls_back_to_general():
    decision = RouteClassifier().classify("今天晚上吃什么？")

    assert decision.selected_route == "general"
    assert decision.filter_tags == []
    assert decision.confidence == 0.0


async def test_query_with_log_uses_route_and_records_score_breakdown():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "SAST 主要服务器有腾讯云、浪潮和 X79 三台",
        DocMetadata(source="/wiki/SAST 设施指南/docx/部署及网络拓扑.md", tags=["SAST 设施指南"]),
    )
    await pipe.ingest_text(
        "软件研发部下设 Web 组和算法组",
        DocMetadata(
            source="/wiki/SAST 说明书 Public 版/docx/软件研发部 介绍.md",
            tags=["SAST 说明书 Public 版"],
        ),
    )

    result, log = await pipe.query_with_log("SAST 有几台服务器？", top_k=3)

    assert result.passages
    assert result.passages[0].metadata.source.endswith("部署及网络拓扑.md")
    assert log.route is not None
    assert log.route.selected_route == "SAST 设施指南"
    assert log.retrieval.passages[0].score_breakdown["final_score"] == result.passages[0].score
    assert log.retrieval.passages[0].source == result.passages[0].metadata.source


async def test_query_with_log_records_controlled_rewrite_without_passage_text():
    pipe = build_pipeline()
    await pipe.ingest_text(
        "部署及网络拓扑：腾讯云、浪潮、X79",
        DocMetadata(source="/wiki/SAST 设施指南/docx/部署及网络拓扑.md", tags=["SAST 设施指南"]),
    )

    _, log = await pipe.query_with_log("SAST 有几台服务器？", top_k=2)

    assert log.rewrite is not None
    assert log.rewrite.original_query == "SAST 有几台服务器？"
    assert log.rewrite.rewritten_query != log.rewrite.original_query
    assert log.rewrite.selected_route == "SAST 设施指南"
    assert "服务器" in log.rewrite.used_aliases
    assert "SAST 设施指南" in log.rewrite.added_terms
    assert "腾讯云" not in log.rewrite.rewritten_query
    assert log.retrieval.query == log.rewrite.rewritten_query


def test_run_log_schema_accepts_answer_stage_log():
    log = RagQueryRunLog(
        query="SAST Link 是什么？",
        retrieval=RagRetrievalLog(query="SAST Link 是什么？", top_k=3),
        answer=RagAnswerLog(
            status="answered",
            answer_text="SAST Link 是统一身份认证系统。",
            citations=(
                RagAnswerCitationLog(
                    source="/wiki/SAST Link/docx/SAST Link 食用指南.md",
                    rank=1,
                    snippet="SAST Link 是统一身份认证系统",
                ),
            ),
        ),
    )

    loaded = RagQueryRunLog.model_validate_json(log.model_dump_json())

    assert loaded.answer is not None
    assert loaded.answer.status == "answered"
    assert loaded.answer.citations[0].rank == 1
