"""InMemoryCosineStore 测试 — 余弦检索 / top_k / tag 过滤 / 持久化。"""

from __future__ import annotations

import pytest

from openagents_orchestration.rag.types import Chunk, DocMetadata
from openagents_orchestration.rag.vectorstore import InMemoryCosineStore


def _chunk(cid: str, text: str, vec: list[float], tags=()) -> Chunk:
    return Chunk(
        id=cid,
        text=text,
        metadata=DocMetadata(source=cid, tags=list(tags)),
        embedding=vec,
    )


def test_add_requires_embedding():
    store = InMemoryCosineStore()
    with pytest.raises(ValueError):
        store.add([Chunk(id="x", text="t", metadata=DocMetadata())])


def test_search_ranks_by_cosine():
    store = InMemoryCosineStore()
    store.add(
        [
            _chunk("a", "A", [1.0, 0.0, 0.0]),
            _chunk("b", "B", [0.0, 1.0, 0.0]),
            _chunk("c", "C", [0.9, 0.1, 0.0]),
        ]
    )
    res = store.search([1.0, 0.0, 0.0], top_k=3)
    assert [p.metadata.source for p in res] == ["a", "c", "b"]


def test_search_top_k_limit():
    store = InMemoryCosineStore()
    store.add([_chunk(str(i), f"t{i}", [1.0, float(i)]) for i in range(5)])
    assert len(store.search([1.0, 0.0], top_k=2)) == 2


def test_search_filter_tags():
    store = InMemoryCosineStore()
    store.add(
        [
            _chunk("a", "A", [1.0, 0.0], tags=("ml",)),
            _chunk("b", "B", [1.0, 0.0], tags=("food",)),
        ]
    )
    res = store.search([1.0, 0.0], top_k=5, filter_tags=["food"])
    assert [p.metadata.source for p in res] == ["b"]


def test_add_rejects_dim_mismatch():
    store = InMemoryCosineStore()
    store.add([_chunk("a", "A", [1.0, 0.0])])
    with pytest.raises(ValueError, match="dim mismatch"):
        store.add([_chunk("b", "B", [1.0, 0.0, 0.0])])


def test_search_rejects_dim_mismatch():
    store = InMemoryCosineStore()
    store.add([_chunk("a", "A", [1.0, 0.0])])
    with pytest.raises(ValueError, match="store dim"):
        store.search([1.0, 0.0, 0.0], top_k=1)


def test_negative_top_k_raises():
    store = InMemoryCosineStore()
    with pytest.raises(ValueError):
        store.search([1.0, 0.0], top_k=-1)


def test_save_load_roundtrip(tmp_path):
    store = InMemoryCosineStore()
    store.add([_chunk("a", "A", [1.0, 0.0], tags=("ml",))])
    f = tmp_path / "vs.json"
    store.save(f)
    store2 = InMemoryCosineStore()
    store2.load(f)
    assert len(store2) == 1
    res = store2.search([1.0, 0.0], top_k=1)
    assert res[0].metadata.source == "a"
    assert res[0].metadata.tags == ["ml"]


def test_empty_store_search():
    assert InMemoryCosineStore().search([1.0], top_k=3) == []
