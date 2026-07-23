"""Retriever 测试 — query → embed → search 端到端(Mock embedding)。"""

from __future__ import annotations

from openagents_orchestration.rag.embedding import MockEmbeddingClient
from openagents_orchestration.rag.retriever import Retriever
from openagents_orchestration.rag.types import Chunk, DocMetadata, Passage
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


async def test_query_lexical_rerank_breaks_vector_tie():
    class TieEmbedding:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    store = InMemoryCosineStore()
    store.add(
        [
            Chunk(
                id="link#0",
                text="SAST Link 是统一身份认证系统",
                metadata=DocMetadata(source="SAST Link 食用指南", tags=["link"]),
                embedding=[1.0, 0.0],
            ),
            Chunk(
                id="rules#0",
                text="SAST 成员守则要求不得泄露内部机密信息",
                metadata=DocMetadata(source="SAST 成员守则", tags=["rules"]),
                embedding=[1.0, 0.0],
            ),
        ]
    )

    res = await Retriever(TieEmbedding(), store).query("成员守则", top_k=2)

    assert res.passages[0].metadata.source == "SAST 成员守则"


async def test_query_uses_wide_internal_vector_pool_for_small_top_k():
    class CaptureStore(InMemoryCosineStore):
        def __init__(self) -> None:
            super().__init__()
            self.seen_top_k = 0

        def search(
            self,
            vector: list[float],
            top_k: int = 5,
            filter_tags: list[str] | None = None,
            required_tags: list[str] | None = None,
        ) -> list[Passage]:
            self.seen_top_k = top_k
            return super().search(vector, top_k, filter_tags, required_tags)

    emb = MockEmbeddingClient()
    store = CaptureStore()
    vecs = await emb.embed(["SAST Evento"] * 20)
    store.add(
        [
            Chunk(
                id=f"c{i}",
                text="SAST Evento",
                metadata=DocMetadata(source=f"doc{i}", tags=["evento"]),
                embedding=vec,
            )
            for i, vec in enumerate(vecs)
        ]
    )

    await Retriever(emb, store).query("SAST Evento 是什么项目？", top_k=3)

    assert store.seen_top_k >= 256


async def test_query_diversifies_repeated_sources_in_top_results():
    class TieEmbedding:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    store = InMemoryCosineStore()
    store.add(
        [
            Chunk(
                id=f"client#{i}",
                text="Evento",
                metadata=DocMetadata(source="SAST Evento 客户端设计", tags=["evento"]),
                embedding=[1.0, 0.0],
            )
            for i in range(3)
        ]
        + [
            Chunk(
                id="base#0",
                text="Evento",
                metadata=DocMetadata(source="SAST Evento", tags=["evento"]),
                embedding=[1.0, 0.0],
            )
        ]
    )

    res = await Retriever(TieEmbedding(), store).query("Evento", top_k=3)

    assert "SAST Evento" in [p.metadata.source for p in res.passages]


async def test_query_prefers_compact_overview_title_for_overview_question():
    class TieEmbedding:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    store = InMemoryCosineStore()
    store.add(
        [
            Chunk(
                id="client#0",
                text="SAST Evento 项目地址 代码结构 用户端",
                metadata=DocMetadata(source="SAST Evento 客户端设计__x.docx", tags=["evento"]),
                embedding=[1.0, 0.0],
            ),
            Chunk(
                id="base#0",
                text="SAST Evento 是一个事件管理系统",
                metadata=DocMetadata(source="SAST Evento__x.docx", tags=["evento"]),
                embedding=[1.0, 0.0],
            ),
        ]
    )

    res = await Retriever(TieEmbedding(), store).query("SAST Evento 是什么项目？", top_k=2)

    assert res.passages[0].metadata.source == "SAST Evento__x.docx"


async def test_query_matches_overview_title_when_query_omits_sast_prefix():
    class TieEmbedding:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    store = InMemoryCosineStore()
    store.add(
        [
            Chunk(
                id="backend#0",
                text="Evento 活动问题",
                metadata=DocMetadata(source="SAST Evento 后端工作安排__x.docx", tags=["evento"]),
                embedding=[1.0, 0.0],
            ),
            Chunk(
                id="base#0",
                text="Evento 活动问题",
                metadata=DocMetadata(source="SAST Evento__x.docx", tags=["evento"]),
                embedding=[1.0, 0.0],
            ),
        ]
    )

    res = await Retriever(TieEmbedding(), store).query("Evento 要解决哪些活动问题？", top_k=2)

    assert res.passages[0].metadata.source == "SAST Evento__x.docx"


async def test_query_prefers_topology_title_for_server_count_question():
    class TieEmbedding:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    store = InMemoryCosineStore()
    store.add(
        [
            Chunk(
                id="self-hosted#0",
                text="服务器 自托管 服务 SAST",
                metadata=DocMetadata(source="自托管服务__x.md", tags=["facilities"]),
                embedding=[1.0, 0.0],
            ),
            Chunk(
                id="topology#0",
                text="服务器 部署 网络 SAST",
                metadata=DocMetadata(source="部署及网络拓扑__x.docx", tags=["facilities"]),
                embedding=[1.0, 0.0],
            ),
        ]
    )

    res = await Retriever(TieEmbedding(), store).query(
        "SAST 有几台服务器？ SAST 设施指南",
        top_k=2,
    )

    assert res.passages[0].metadata.source == "部署及网络拓扑__x.docx"
