"""RAG 检索过程评测 — hit@k / MRR / source 回归。

先评 retrieval 阶段,把 badcase 固化成可回归资产。生成侧忠实性评测后续再接。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol


class Queryable(Protocol):
    async def query(
        self,
        text: str,
        top_k: int = 5,
        filter_tags: list[str] | None = None,
        required_tags: list[str] | None = None,
    ): ...


@dataclass(frozen=True)
class RetrievalEvalCase:
    question: str
    expected_sources: tuple[str, ...]
    top_k: int = 3
    filter_tags: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    route: str = "general"
    query_type: str = "general"
    failure_cause: str = "retrieval"


@dataclass(frozen=True)
class RetrievalTracePassage:
    rank: int
    source: str
    score: float
    snippet: str


@dataclass(frozen=True)
class RetrievalEvalItem:
    case: RetrievalEvalCase
    trace: tuple[RetrievalTracePassage, ...]
    hit_rank: int | None
    matched_source: str | None = None
    matched_expected_source: str | None = None

    @property
    def hit(self) -> bool:
        return self.hit_rank is not None

    @property
    def reciprocal_rank(self) -> float:
        return 0.0 if self.hit_rank is None else 1.0 / self.hit_rank

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(item.source for item in self.trace)

    @property
    def top_score(self) -> float:
        return 0.0 if not self.trace else self.trace[0].score

    @property
    def hit_score(self) -> float | None:
        if self.hit_rank is None:
            return None
        return self.trace[self.hit_rank - 1].score

    @property
    def score_gap_to_hit(self) -> float | None:
        if self.hit_score is None:
            return None
        return self.top_score - self.hit_score

    @property
    def precision_at_k(self) -> float:
        if not self.trace:
            return 0.0
        relevant = sum(
            1
            for passage in self.trace
            if _source_matches(passage.source, self.case.expected_sources)
        )
        return relevant / len(self.trace)

    @property
    def recalled_expected_sources(self) -> tuple[str, ...]:
        recalled: list[str] = []
        for expected in self.case.expected_sources:
            if any(expected in passage.source for passage in self.trace):
                recalled.append(expected)
        return tuple(recalled)

    @property
    def recall_at_k(self) -> float:
        if not self.case.expected_sources:
            return 0.0
        return len(self.recalled_expected_sources) / len(self.case.expected_sources)

    @property
    def ndcg_at_k(self) -> float:
        if self.hit_rank is None:
            return 0.0
        return 1.0 / math.log2(self.hit_rank + 1)

    @property
    def unique_source_count(self) -> int:
        return len(set(self.sources))

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.case.question,
            "expected_sources": list(self.case.expected_sources),
            "filter_tags": list(self.case.filter_tags),
            "required_tags": list(self.case.required_tags),
            "route": self.case.route,
            "query_type": self.case.query_type,
            "failure_cause": self.case.failure_cause,
            "top_k": self.case.top_k,
            "hit": self.hit,
            "hit_rank": self.hit_rank,
            "matched_source": self.matched_source,
            "matched_expected_source": self.matched_expected_source,
            "recalled_expected_sources": list(self.recalled_expected_sources),
            "top_score": self.top_score,
            "hit_score": self.hit_score,
            "score_gap_to_hit": self.score_gap_to_hit,
            "precision_at_k": self.precision_at_k,
            "recall_at_k": self.recall_at_k,
            "ndcg_at_k": self.ndcg_at_k,
            "unique_source_count": self.unique_source_count,
            "trace": [
                {
                    "rank": passage.rank,
                    "source": passage.source,
                    "score": passage.score,
                    "snippet": passage.snippet,
                }
                for passage in self.trace
            ],
        }


@dataclass(frozen=True)
class RetrievalEvalSummary:
    items: tuple[RetrievalEvalItem, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def hits(self) -> int:
        return sum(1 for item in self.items if item.hit)

    @property
    def hit_rate(self) -> float:
        return 0.0 if not self.items else self.hits / len(self.items)

    @property
    def miss_rate(self) -> float:
        return 1.0 - self.hit_rate if self.items else 0.0

    @property
    def top1_accuracy(self) -> float:
        if not self.items:
            return 0.0
        return sum(1 for item in self.items if item.hit_rank == 1) / len(self.items)

    @property
    def mrr(self) -> float:
        if not self.items:
            return 0.0
        return sum(item.reciprocal_rank for item in self.items) / len(self.items)

    @property
    def avg_hit_rank(self) -> float:
        ranks = [item.hit_rank for item in self.items if item.hit_rank is not None]
        return 0.0 if not ranks else sum(ranks) / len(ranks)

    @property
    def precision_at_k(self) -> float:
        return _avg(item.precision_at_k for item in self.items)

    @property
    def recall_at_k(self) -> float:
        return _avg(item.recall_at_k for item in self.items)

    @property
    def ndcg_at_k(self) -> float:
        return _avg(item.ndcg_at_k for item in self.items)

    @property
    def avg_unique_sources(self) -> float:
        return _avg(item.unique_source_count for item in self.items)

    @property
    def avg_score_gap_to_hit(self) -> float:
        return _avg(
            item.score_gap_to_hit
            for item in self.items
            if item.score_gap_to_hit is not None
        )

    def hit_rate_by_query_type(self) -> dict[str, float]:
        return _hit_rate_by(lambda item: item.case.query_type, self.items)

    def hit_rate_by_route(self) -> dict[str, float]:
        return _hit_rate_by(lambda item: item.case.route, self.items)

    def hit_rate_by_failure_cause(self) -> dict[str, float]:
        return _hit_rate_by(lambda item: item.case.failure_cause, self.items)

    def breakdown_by_query_type(self) -> dict[str, dict[str, float | int]]:
        return _breakdown_by(lambda item: item.case.query_type, self.items)

    def breakdown_by_route(self) -> dict[str, dict[str, float | int]]:
        return _breakdown_by(lambda item: item.case.route, self.items)

    def breakdown_by_failure_cause(self) -> dict[str, dict[str, float | int]]:
        return _breakdown_by(lambda item: item.case.failure_cause, self.items)

    def misses_by_failure_cause(self) -> dict[str, int]:
        return _misses_by(lambda item: item.case.failure_cause, self.items)

    def misses_by_route(self) -> dict[str, int]:
        return _misses_by(lambda item: item.case.route, self.items)

    def misses_by_query_type(self) -> dict[str, int]:
        return _misses_by(lambda item: item.case.query_type, self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": _metrics_for(self.items),
            "by_query_type": self.breakdown_by_query_type(),
            "by_route": self.breakdown_by_route(),
            "by_failure_cause": self.breakdown_by_failure_cause(),
            "misses_by_failure_cause": self.misses_by_failure_cause(),
            "misses_by_route": self.misses_by_route(),
            "misses_by_query_type": self.misses_by_query_type(),
            "items": [item.to_dict() for item in self.items],
        }


def _misses_by(key_fn, items: tuple[RetrievalEvalItem, ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        if not item.hit:
            counts[key_fn(item)] += 1
    return dict(counts)


async def evaluate_retrieval(
    pipe: Queryable,
    cases: list[RetrievalEvalCase],
    default_top_k: int = 3,
) -> RetrievalEvalSummary:
    items: list[RetrievalEvalItem] = []
    for case in cases:
        top_k = case.top_k or default_top_k
        filter_tags = list(case.filter_tags) if case.filter_tags else None
        required_tags = list(case.required_tags) if case.required_tags else None
        result = await pipe.query(
            case.question,
            top_k=top_k,
            filter_tags=filter_tags,
            required_tags=required_tags,
        )
        trace = _trace_passages(result.passages)
        match = _first_hit(tuple(item.source for item in trace), case.expected_sources)
        hit_rank, matched_source, matched_expected_source = match
        items.append(
            RetrievalEvalItem(
                case=case,
                trace=trace,
                hit_rank=hit_rank,
                matched_source=matched_source,
                matched_expected_source=matched_expected_source,
            )
        )
    return RetrievalEvalSummary(items=tuple(items))


def _first_hit(
    sources: tuple[str, ...], expected: tuple[str, ...]
) -> tuple[int | None, str | None, str | None]:
    for idx, source in enumerate(sources, start=1):
        expected_source = _matched_expected_source(source, expected)
        if expected_source is not None:
            return idx, source, expected_source
    return None, None, None


def _source_matches(source: str, expected: tuple[str, ...]) -> bool:
    return _matched_expected_source(source, expected) is not None


def _matched_expected_source(source: str, expected: tuple[str, ...]) -> str | None:
    for part in expected:
        if part in source:
            return part
    return None


def _trace_passages(passages) -> tuple[RetrievalTracePassage, ...]:
    return tuple(
        RetrievalTracePassage(
            rank=idx,
            source=passage.metadata.source,
            score=passage.score,
            snippet=_snippet(passage.text),
        )
        for idx, passage in enumerate(passages, start=1)
    )


def _snippet(text: str, max_chars: int = 180) -> str:
    return " ".join(text.split())[:max_chars]


def _hit_rate_by(key_fn, items: tuple[RetrievalEvalItem, ...]) -> dict[str, float]:
    buckets: dict[str, list[RetrievalEvalItem]] = defaultdict(list)
    for item in items:
        buckets[key_fn(item)].append(item)
    return {
        key: sum(1 for item in bucket if item.hit) / len(bucket)
        for key, bucket in buckets.items()
    }


def _breakdown_by(key_fn, items: tuple[RetrievalEvalItem, ...]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[RetrievalEvalItem]] = defaultdict(list)
    for item in items:
        buckets[key_fn(item)].append(item)
    return {key: _metrics_for(tuple(bucket)) for key, bucket in buckets.items()}


def _metrics_for(items: tuple[RetrievalEvalItem, ...]) -> dict[str, float | int]:
    total = len(items)
    hits = sum(1 for item in items if item.hit)
    return {
        "total": total,
        "hits": hits,
        "misses": total - hits,
        "hit_rate": 0.0 if not total else hits / total,
        "miss_rate": 0.0 if not total else (total - hits) / total,
        "top1_accuracy": 0.0
        if not total
        else sum(1 for item in items if item.hit_rank == 1) / total,
        "mrr": _avg(item.reciprocal_rank for item in items),
        "avg_hit_rank": _avg(
            item.hit_rank for item in items if item.hit_rank is not None
        ),
        "precision_at_k": _avg(item.precision_at_k for item in items),
        "recall_at_k": _avg(item.recall_at_k for item in items),
        "ndcg_at_k": _avg(item.ndcg_at_k for item in items),
        "avg_unique_sources": _avg(item.unique_source_count for item in items),
        "avg_score_gap_to_hit": _avg(
            item.score_gap_to_hit
            for item in items
            if item.score_gap_to_hit is not None
        ),
    }


def _avg(values) -> float:
    nums = [value for value in values if value is not None]
    return 0.0 if not nums else sum(nums) / len(nums)
