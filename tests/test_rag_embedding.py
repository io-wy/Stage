"""Embedding 客户端测试 — Mock 确定性/语义 + Httpx OpenAI 格式解析(mock HTTP)。"""

from __future__ import annotations

import math

import pytest

from openagents_orchestration.rag.embedding import (
    HttpxEmbeddingClient,
    MockEmbeddingClient,
)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


async def test_mock_deterministic():
    emb = MockEmbeddingClient()
    v1 = await emb.embed(["机器 学习 模型"])
    v2 = await emb.embed(["机器 学习 模型"])
    assert v1 == v2


async def test_mock_normalized_and_dim():
    emb = MockEmbeddingClient(dim=32)
    (v,) = await emb.embed(["hello world"])
    assert len(v) == 32
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-6


async def test_mock_semantic_closeness():
    emb = MockEmbeddingClient()
    a, b, c = await emb.embed(["机器 学习 模型", "机器 学习 训练", "烹饪 食谱 蛋糕"])
    assert _cos(a, b) > _cos(a, c)  # 共享 token 多的更近


def test_httpx_requires_base(monkeypatch):
    monkeypatch.delenv("RAG_EMBED_BASE", raising=False)
    with pytest.raises(ValueError):
        HttpxEmbeddingClient()


async def test_httpx_parses_openai_format(monkeypatch):
    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {"index": 1, "embedding": [0.2, 0.2]},
                    {"index": 0, "embedding": [0.1, 0.1]},
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            assert url.endswith("/embeddings")
            assert headers["Authorization"] == "Bearer k"
            return FakeResp()

    monkeypatch.setattr(
        "openagents_orchestration.rag.embedding.httpx.AsyncClient", FakeClient
    )
    client = HttpxEmbeddingClient(base="http://x/v1", api_key="k", model="m")
    vecs = await client.embed(["a", "b"])
    assert vecs == [[0.1, 0.1], [0.2, 0.2]]  # 按 index 排序对齐输入顺序


async def test_httpx_rejects_count_mismatch(monkeypatch):
    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"data": [{"index": 0, "embedding": [0.1]}]}  # 只返回 1 个,请求 2 个

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(
        "openagents_orchestration.rag.embedding.httpx.AsyncClient", FakeClient
    )
    client = HttpxEmbeddingClient(base="http://x/v1", api_key="k", model="m")
    with pytest.raises(RuntimeError, match="returned 1 vectors for 2 texts"):
        await client.embed(["a", "b"])
