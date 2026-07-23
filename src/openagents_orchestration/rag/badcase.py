"""Badcase sediment format for retrieval regression.

Badcase records are intentionally JSONL-friendly and store only controlled
metadata plus retrieval trace. They can be converted back into eval cases once
the expected source is confirmed.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from time import time
from uuid import uuid4

from pydantic import BaseModel, Field

from openagents_orchestration.rag.eval import RetrievalEvalCase
from openagents_orchestration.rag.runlog import RagQueryRunLog, RagRetrievalPassageLog


class BadcaseTracePassage(BaseModel):
    rank: int
    source: str
    score: float
    snippet: str = ""
    tags: tuple[str, ...] = ()
    score_breakdown: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_passage_log(cls, passage: RagRetrievalPassageLog) -> BadcaseTracePassage:
        return cls(
            rank=passage.rank,
            source=passage.source,
            score=passage.score,
            snippet=passage.snippet,
            tags=tuple(passage.tags),
            score_breakdown=dict(passage.score_breakdown),
        )


class BadcaseRecord(BaseModel):
    schema_version: str = "rag.badcase.v1"
    case_id: str = Field(default_factory=lambda: str(uuid4()))
    log_id: str | None = None
    created_at: float = Field(default_factory=time)
    question: str
    retrieval_query: str = ""
    expected_sources: tuple[str, ...]
    route: str = "general"
    query_type: str = "general"
    failure_cause: str = "retrieval"
    top_k: int = 3
    filter_tags: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    observed_sources: tuple[str, ...] = ()
    trace: tuple[BadcaseTracePassage, ...] = ()
    fix_note: str = ""

    @classmethod
    def from_query_log(
        cls,
        log: RagQueryRunLog,
        expected_sources: tuple[str, ...],
        query_type: str = "general",
        failure_cause: str = "retrieval",
        fix_note: str = "",
    ) -> BadcaseRecord:
        route = log.route.selected_route if log.route is not None else "general"
        trace = tuple(
            BadcaseTracePassage.from_passage_log(passage)
            for passage in log.retrieval.passages
        )
        return cls(
            log_id=log.run_id,
            question=log.query,
            retrieval_query=log.retrieval.query or log.query,
            expected_sources=tuple(expected_sources),
            route=route,
            query_type=query_type,
            failure_cause=failure_cause,
            top_k=log.retrieval.top_k,
            filter_tags=tuple(log.retrieval.filter_tags),
            required_tags=tuple(log.retrieval.required_tags),
            observed_sources=tuple(passage.source for passage in log.retrieval.passages),
            trace=trace,
            fix_note=fix_note,
        )

    def to_eval_case(self) -> RetrievalEvalCase:
        return RetrievalEvalCase(
            question=self.question,
            expected_sources=self.expected_sources,
            top_k=self.top_k,
            filter_tags=self.filter_tags,
            required_tags=self.required_tags,
            route=self.route,
            query_type=self.query_type,
            failure_cause=self.failure_cause,
        )


def dump_badcases_jsonl(records: Iterable[BadcaseRecord], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [record.model_dump_json() for record in records]
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_badcases_jsonl(path: str | Path) -> tuple[BadcaseRecord, ...]:
    source = Path(path)
    records: list[BadcaseRecord] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(BadcaseRecord.model_validate_json(line))
    return tuple(records)


def badcases_to_eval_cases(
    records: Iterable[BadcaseRecord],
) -> tuple[RetrievalEvalCase, ...]:
    return tuple(record.to_eval_case() for record in records)
