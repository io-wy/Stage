"""Retriever 测试 — query → embed → search 端到端(Mock embedding)。"""

from __future__ import annotations

from openagents_orchestration.rag.embedding import MockEmbeddingClient
from openagents_orchestration.rag.retriever import Retriever
from openagents_orchestration.rag.types import Chunk, DocMetadata
from openagents_orchestration.rag.vectorstore import InMemoryCosineStore


async def _make_store(emb: MockEmbeddingClient) -> InMemoryCosineStore:
    store = InMemoryCosineStore()
    docs = [
        ("ml", "机器 学习 深度 模型 训练", ["ml"]),
        ("food", "烹饪 食谱 烘焙 蛋糕 食材", ["food"]),
    ]
    vecs = await emb.embed([d[1] for d in docs])
    store.add(
        [
            Chunk(id=cid, text=t, metadata=DocMetadata(source=cid, tags=tags), embedding=v)
            for (cid, t, tags), v in zip(docs, vecs, strict=True)
        ]
    )
    return store


async def test_query_returns_relevant_first():
    emb = MockEmbeddingClient()
    store = await _make_store(emb)
    res = await Retriever(emb, store).query("机器 学习 模型", top_k=2)
    assert res.passages[0].metadata.source == "ml"


async def test_query_empty_returns_empty():
    res = await Retriever(MockEmbeddingClient(), InMemoryCosineStore()).query("   ")
    assert res.passages == []


async def test_query_filter_tags():
    emb = MockEmbeddingClient()
    store = await _make_store(emb)
    res = await Retriever(emb, store).query("机器 学习", top_k=2, filter_tags=["food"])
    assert all(p.metadata.source == "food" for p in res.passages)
