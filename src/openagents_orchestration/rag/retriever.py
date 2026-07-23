"""检索器 — query → embed → search → top_k(带溯源)。

向量检索负责语义召回,词法重排负责把标题 / 专名 / 路径名拉上来。
query 改写 / 更重的 rerank 留扩展点,但现在先把混合检索补上。
"""

from __future__ import annotations

import math
import re
from collections import Counter

from openagents_orchestration.rag.embedding import EmbeddingClient
from openagents_orchestration.rag.types import Chunk, RetrievalResult
from openagents_orchestration.rag.vectorstore import VectorStore

_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_BM25_K1 = 1.5
_BM25_B = 0.75
_VECTOR_WEIGHT = 0.72
_LEXICAL_WEIGHT = 0.28
_SOURCE_BONUS_PER_TOKEN = 0.10
_SOURCE_BONUS_MAX = 0.35
_GENERIC_SOURCE_TOKENS = {"sast", "校科", "科协", "校科协"}
_MIN_VECTOR_CANDIDATES = 256
_OVERVIEW_INTENT_TERMS = (
    "是什么",
    "什么项目",
    "做什么",
    "负责什么",
    "有什么用",
    "要解决",
    "解决哪些",
    "哪些活动问题",
)
_SUBTOPIC_TERMS = (
    "客户端",
    "后端",
    "前端",
    "desktop",
    "接口",
    "工作安排",
    "设计物料",
)
_LECTURE_TITLE_TERMS = ("公开授课", "公开课", "授课记录")
_DEPARTMENT_QUESTION_TERMS = ("是什么", "做什么", "负责", "有哪些组")
_SERVER_COUNT_TERMS = ("几台服务器", "多少台服务器", "服务器数量", "有几台")


class Retriever:
    """向量检索 + tag 过滤。扩展点:query 改写 / rerank / 混合检索(待数据)。"""

    def __init__(self, embedding: EmbeddingClient, store: VectorStore):
        self._embedding = embedding
        self._store = store

    async def query(
        self,
        text: str,
        top_k: int = 5,
        filter_tags: list[str] | None = None,
        required_tags: list[str] | None = None,
    ) -> RetrievalResult:
        text = text.strip()
        if not text:
            return RetrievalResult(query=text, passages=[])
        # TODO(待数据): query 改写(主改写 / 子查询 / 同义),提升召回
        vectors = await self._embedding.embed([text])
        qvec = vectors[0]

        query_tokens = _tokenize(text)
        chunks = self._candidate_chunks(filter_tags, required_tags)
        lexical = self._score_lexical(query_tokens, chunks)

        vector_top_k = max(_MIN_VECTOR_CANDIDATES, top_k * 8, top_k)
        vec_passages = self._store.search(
            qvec,
            top_k=vector_top_k,
            filter_tags=filter_tags,
            required_tags=required_tags,
        )
        vec_scores = {self._passage_key(p): p.score for p in vec_passages}
        lex_scores = lexical

        vmax = max(vec_scores.values(), default=0.0) or 1.0
        lmax = max(lex_scores.values(), default=0.0) or 1.0

        merged: dict[str, tuple[str, float, Chunk, dict[str, float]]] = {}
        for c in chunks:
            key = c.id
            vscore = vec_scores.get(key, 0.0) / vmax
            lscore = lex_scores.get(key, 0.0) / lmax
            source_bonus = _source_bonus(text, c)
            title_adjustment = _title_intent_adjustment(text, c)
            vector_component = _VECTOR_WEIGHT * vscore
            lexical_component = _LEXICAL_WEIGHT * lscore
            score = vector_component + lexical_component + source_bonus + title_adjustment
            breakdown = {
                "vector_score": vscore,
                "lexical_score": lscore,
                "vector_component": vector_component,
                "lexical_component": lexical_component,
                "source_bonus": source_bonus,
                "title_adjustment": title_adjustment,
                "final_score": score,
            }
            merged[key] = (c.text, score, c, breakdown)

        for p in vec_passages:
            key = self._passage_key(p)
            if key in merged:
                text_, score, chunk, breakdown = merged[key]
                source_bonus = _source_bonus(text, chunk)
                title_adjustment = _title_intent_adjustment(text, chunk)
                lexical_component = _LEXICAL_WEIGHT * lex_scores.get(key, 0.0) / lmax
                vector_score = (
                    p.score * _VECTOR_WEIGHT
                    + lexical_component
                    + source_bonus
                    + title_adjustment
                )
                if vector_score > score:
                    breakdown = {
                        "vector_score": p.score,
                        "lexical_score": lex_scores.get(key, 0.0) / lmax,
                        "vector_component": p.score * _VECTOR_WEIGHT,
                        "lexical_component": lexical_component,
                        "source_bonus": source_bonus,
                        "title_adjustment": title_adjustment,
                        "final_score": vector_score,
                    }
                    score = vector_score
                merged[key] = (text_, score, chunk, breakdown)

        passages = [
            self._make_passage(text_, score, chunk, breakdown)
            for text_, score, chunk, breakdown in merged.values()
        ]
        passages.sort(key=lambda p: p.score, reverse=True)
        return RetrievalResult(query=text, passages=_diversify_by_source(passages, top_k))

    def _candidate_chunks(
        self, filter_tags: list[str] | None, required_tags: list[str] | None
    ) -> list[Chunk]:
        chunks = getattr(self._store, "iter_chunks", lambda: [])()
        if filter_tags:
            want = set(filter_tags)
            chunks = [c for c in chunks if want & set(c.metadata.tags)]
        if required_tags:
            required = set(required_tags)
            chunks = [c for c in chunks if required <= set(c.metadata.tags)]
        return chunks

    def _score_lexical(self, query_tokens: list[str], chunks: list[Chunk]) -> dict[str, float]:
        if not query_tokens or not chunks:
            return {}
        docs: dict[str, list[str]] = {}
        df: Counter[str] = Counter()
        for c in chunks:
            toks = _tokenize(c.text)
            docs[c.id] = toks
            df.update(set(toks))
        n_docs = len(chunks)
        avgdl = sum(len(toks) for toks in docs.values()) / n_docs if n_docs else 1.0
        query_counts = Counter(query_tokens)
        scores: dict[str, float] = {}
        for cid, toks in docs.items():
            tf = Counter(toks)
            dl = len(toks) or 1
            score = 0.0
            for tok in query_counts:
                freq = tf.get(tok, 0)
                if not freq:
                    continue
                idf = math.log1p((n_docs - df[tok] + 0.5) / (df[tok] + 0.5))
                denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * (dl / avgdl))
                score += idf * (freq * (_BM25_K1 + 1)) / denom
            if score:
                scores[cid] = score
        return scores

    @staticmethod
    def _passage_key(passage) -> str:
        return passage.metadata.extra.get("chunk_id") or passage.metadata.source or passage.text

    @staticmethod
    def _make_passage(text: str, score: float, chunk: Chunk, breakdown: dict[str, float]):
        from openagents_orchestration.rag.types import Passage

        metadata = chunk.metadata.model_copy(deep=True)
        breakdown["final_score"] = score
        metadata.extra["score_breakdown"] = breakdown
        return Passage(text=text, score=score, metadata=metadata)


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(text):
        tok = match.group(0).lower()
        if _is_cjk(tok):
            chars = [ch for ch in tok if "\u4e00" <= ch <= "\u9fff"]
            tokens.extend(chars)
            tokens.extend(a + b for a, b in zip(chars, chars[1:], strict=False))
        else:
            tokens.append(tok)
    return tokens


def _is_cjk(token: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in token)


def _source_bonus(query: str, chunk: Chunk) -> float:
    source = chunk.metadata.source
    if not source:
        return 0.0
    query_tokens = _source_specific_tokens(query)
    source_tokens = _source_specific_tokens(source)
    hits = len(query_tokens & source_tokens)
    return min(_SOURCE_BONUS_MAX, hits * _SOURCE_BONUS_PER_TOKEN)


def _title_intent_adjustment(query: str, chunk: Chunk) -> float:
    title = _source_title(chunk.metadata.source)
    if not title:
        return 0.0
    return (
        _overview_title_adjustment(query, title)
        + _department_title_adjustment(query, title)
        + _server_inventory_title_adjustment(query, title)
    )


def _overview_title_adjustment(query: str, title: str) -> float:
    if not _has_any(query, _OVERVIEW_INTENT_TERMS) or _has_any(query, _SUBTOPIC_TERMS):
        return 0.0
    score = 0.0
    compact_query = _compact(query)
    compact_title = _compact(title)
    if (compact_title and compact_title in compact_query) or any(
        core in compact_query for core in _compact_title_cores(title)
    ):
        score += 0.55
    elif _latin_terms(title) and _latin_terms(title) <= _latin_terms(query):
        score += 0.30
    if _has_any(title, _SUBTOPIC_TERMS):
        score -= 0.35
    return score


def _department_title_adjustment(query: str, title: str) -> float:
    departments = _department_terms(query)
    if not departments or not _has_any(query, _DEPARTMENT_QUESTION_TERMS):
        return 0.0

    score = 0.0
    if any(dept in title for dept in departments):
        score += 0.45 if "介绍" in title else 0.18
    elif _has_any(title, _LECTURE_TITLE_TERMS):
        score -= 0.45

    if "组" in query and ("组介绍" in title or "group introduction" in title.lower()):
        score += 0.12
    return score


def _server_inventory_title_adjustment(query: str, title: str) -> float:
    if "服务器" not in query or not _has_any(query, _SERVER_COUNT_TERMS):
        return 0.0
    if "拓扑" in title or ("部署" in title and "网络" in title):
        return 0.45
    return 0.0


def _diversify_by_source(passages: list, top_k: int) -> list:
    if top_k <= 0:
        return []
    selected: list = []
    selected_ids: set[int] = set()
    seen_sources: set[str] = set()
    for passage in passages:
        source = passage.metadata.source or passage.text
        if source in seen_sources:
            continue
        selected.append(passage)
        selected_ids.add(id(passage))
        seen_sources.add(source)
        if len(selected) >= top_k:
            return selected

    for passage in passages:
        if id(passage) in selected_ids:
            continue
        selected.append(passage)
        if len(selected) >= top_k:
            break
    return selected


def _source_specific_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in _tokenize(text)
        if len(tok) > 1 and tok not in _GENERIC_SOURCE_TOKENS
    }


def _source_title(source: str) -> str:
    if not source:
        return ""
    name = source.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    return stem.split("__", 1)[0].strip()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _compact_title_cores(title: str) -> set[str]:
    compact_title = _compact(title)
    cores: set[str] = set()
    for prefix in ("sast",):
        if compact_title.startswith(prefix):
            core = compact_title[len(prefix) :]
            if len(core) >= 3:
                cores.add(core)
    return cores


def _latin_terms(text: str) -> set[str]:
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z][A-Za-z0-9+#.]*", text)}


def _department_terms(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9+#]+部", text))
