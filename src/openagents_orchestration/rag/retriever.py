"""检索器 — query → embed → search → top_k(带溯源)。

最小闭环只做向量检索 + tag 过滤。query 改写 / rerank / 混合检索(BM25)留扩展点,
照《工业级的 RAG 优化选型》「重武器等飞轮转起来」——有真实数据与 badcase 后再加。
"""

from __future__ import annotations

from openagents_orchestration.rag.embedding import EmbeddingClient
from openagents_orchestration.rag.types import RetrievalResult
from openagents_orchestration.rag.vectorstore import VectorStore


class Retriever:
    """向量检索 + tag 过滤。扩展点:query 改写 / rerank / 混合检索(待数据)。"""

    def __init__(self, embedding: EmbeddingClient, store: VectorStore):
        self._embedding = embedding
        self._store = store

    async def query(
        self, text: str, top_k: int = 5, filter_tags: list[str] | None = None
    ) -> RetrievalResult:
        text = text.strip()
        if not text:
            return RetrievalResult(query=text, passages=[])
        # TODO(待数据): query 改写(主改写 / 子查询 / 同义),提升召回
        # TODO(待数据): 混合检索(向量 + BM25),补专名 / 字段 / 精确匹配
        vectors = await self._embedding.embed([text])
        passages = self._store.search(vectors[0], top_k=top_k, filter_tags=filter_tags)
        # TODO(待数据): rerank(重排序),把最有用证据排到最前
        return RetrievalResult(query=text, passages=passages)
