"""RAG query / retrieval / answer 运行日志 schema。

当前先覆盖 query + route + retrieval。answer 阶段接入后复用同一个 run_id,继续补
answer/citation/refusal 字段。
"""

from __future__ import annotations

from time import time
from uuid import uuid4

from pydantic import BaseModel, Field

from openagents_orchestration.rag.rewrite import QueryRewriteDecision
from openagents_orchestration.rag.route import RouteDecision
from openagents_orchestration.rag.types import RetrievalResult


class RagRetrievalPassageLog(BaseModel):
    rank: int
    source: str
    score: float
    tags: list[str] = Field(default_factory=list)
    snippet: str = ""
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class RagRetrievalLog(BaseModel):
    query: str = ""
    top_k: int
    filter_tags: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)
    passages: list[RagRetrievalPassageLog] = Field(default_factory=list)


class RagAnswerCitationLog(BaseModel):
    source: str
    rank: int | None = None
    snippet: str = ""


class RagAnswerLog(BaseModel):
    status: str = "not_run"
    answer_text: str = ""
    citations: tuple[RagAnswerCitationLog, ...] = ()
    refusal_reason: str = ""
    errors: list[str] = Field(default_factory=list)


class RagQueryRunLog(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    started_at: float = Field(default_factory=time)
    finished_at: float | None = None
    route: RouteDecision | None = None
    rewrite: QueryRewriteDecision | None = None
    retrieval: RagRetrievalLog
    answer: RagAnswerLog | None = None
    errors: list[str] = Field(default_factory=list)


def make_retrieval_log(
    result: RetrievalResult,
    top_k: int,
    filter_tags: list[str] | None,
    required_tags: list[str] | None,
) -> RagRetrievalLog:
    return RagRetrievalLog(
        query=result.query,
        top_k=top_k,
        filter_tags=filter_tags or [],
        required_tags=required_tags or [],
        passages=[
            RagRetrievalPassageLog(
                rank=idx,
                source=passage.metadata.source,
                score=passage.score,
                tags=passage.metadata.tags,
                snippet=" ".join(passage.text.split())[:180],
                score_breakdown=passage.metadata.extra.get("score_breakdown", {}),
            )
            for idx, passage in enumerate(result.passages, start=1)
        ],
    )
