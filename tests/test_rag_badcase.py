"""RAG badcase 沉淀格式测试。"""

from __future__ import annotations

from openagents_orchestration.rag import (
    BadcaseRecord,
    RagQueryRunLog,
    RagRetrievalLog,
    RagRetrievalPassageLog,
    RouteDecision,
    badcases_to_eval_cases,
    dump_badcases_jsonl,
    load_badcases_jsonl,
)


def test_badcase_from_query_log_converts_to_eval_case():
    log = RagQueryRunLog(
        run_id="run-1",
        query="SAST 有几台服务器？",
        route=RouteDecision(
            selected_route="SAST 设施指南",
            confidence=1.0,
            candidates=["SAST 设施指南"],
            filter_tags=["SAST 设施指南"],
            matched_aliases=["服务器"],
        ),
        retrieval=RagRetrievalLog(
            query="SAST 有几台服务器？ SAST 设施指南",
            top_k=3,
            filter_tags=["SAST 设施指南"],
            required_tags=["perm:sensitive"],
            passages=[
                RagRetrievalPassageLog(
                    rank=1,
                    source="/wiki/SAST 设施指南/docx/自托管服务.md",
                    score=1.0,
                    tags=["SAST 设施指南", "perm:sensitive"],
                    snippet="自托管服务",
                    score_breakdown={"final_score": 1.0},
                )
            ],
        ),
    )

    badcase = BadcaseRecord.from_query_log(
        log,
        expected_sources=("部署及网络拓扑",),
        query_type="设施问答",
        failure_cause="retrieval",
        fix_note="应该召回部署及网络拓扑中的服务器列表",
    )

    assert badcase.question == "SAST 有几台服务器？"
    assert badcase.retrieval_query == "SAST 有几台服务器？ SAST 设施指南"
    assert badcase.log_id == "run-1"
    assert badcase.route == "SAST 设施指南"
    assert badcase.filter_tags == ("SAST 设施指南",)
    assert badcase.required_tags == ("perm:sensitive",)
    assert badcase.observed_sources == ("/wiki/SAST 设施指南/docx/自托管服务.md",)
    assert badcase.trace[0].score_breakdown == {"final_score": 1.0}

    eval_case = badcase.to_eval_case()

    assert eval_case.question == badcase.question
    assert eval_case.expected_sources == ("部署及网络拓扑",)
    assert eval_case.top_k == 3
    assert eval_case.filter_tags == ("SAST 设施指南",)
    assert eval_case.required_tags == ("perm:sensitive",)
    assert eval_case.route == "SAST 设施指南"
    assert eval_case.query_type == "设施问答"
    assert eval_case.failure_cause == "retrieval"


def test_badcase_json_roundtrip():
    badcase = BadcaseRecord(
        question="NAS 怎么访问？",
        expected_sources=("NAS",),
        route="SAST 设施指南",
        query_type="设施问答",
        failure_cause="route",
        top_k=3,
        filter_tags=("SAST 设施指南",),
        required_tags=("perm:internal",),
    )

    dumped = badcase.model_dump_json()
    loaded = BadcaseRecord.model_validate_json(dumped)

    assert loaded == badcase


def test_badcase_jsonl_roundtrip_and_eval_conversion(tmp_path):
    path = tmp_path / "badcases.jsonl"
    records = (
        BadcaseRecord(
            question="SAST 有几台服务器？",
            retrieval_query="SAST 有几台服务器？ SAST 设施指南",
            expected_sources=("部署及网络拓扑",),
            route="SAST 设施指南",
            query_type="设施问答",
            failure_cause="retrieval",
            top_k=3,
            filter_tags=("SAST 设施指南",),
            required_tags=("perm:sensitive",),
        ),
        BadcaseRecord(
            question="怎么注册 SAST Link 账号？",
            expected_sources=("SAST Link 食用指南",),
            route="SAST Link",
            query_type="操作步骤",
            failure_cause="chunk",
            top_k=5,
            filter_tags=("SAST Link",),
        ),
    )

    dump_badcases_jsonl(records, path)
    loaded = load_badcases_jsonl(path)
    eval_cases = badcases_to_eval_cases(loaded)

    assert loaded == records
    assert len(eval_cases) == 2
    assert eval_cases[0].question == "SAST 有几台服务器？"
    assert eval_cases[0].expected_sources == ("部署及网络拓扑",)
    assert eval_cases[0].top_k == 3
    assert eval_cases[0].required_tags == ("perm:sensitive",)
    assert eval_cases[1].failure_cause == "chunk"
