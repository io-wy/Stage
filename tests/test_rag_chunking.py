"""StructuralChunker 测试 — 结构切分 / 合并 / overlap / metadata 隔离。"""

from __future__ import annotations

from openagents_orchestration.rag.chunking import StructuralChunker
from openagents_orchestration.rag.types import DocMetadata


def test_splits_on_headings():
    text = "# 案例一\n客户A的背景与方案。\n\n# 案例二\n客户B的背景与方案。"
    chunks = StructuralChunker().chunk(text)
    assert len(chunks) == 2
    assert chunks[0].text.startswith("# 案例一")
    assert chunks[1].text.startswith("# 案例二")


def test_merges_small_blocks():
    text = "短段一\n\n短段二\n\n短段三"
    chunks = StructuralChunker(min_chars=120).chunk(text)
    assert len(chunks) == 1
    assert "短段一" in chunks[0].text and "短段三" in chunks[0].text


def test_splits_oversize_with_overlap():
    long = "。".join(f"第{i}句内容比较长一些" for i in range(120))
    chunks = StructuralChunker(max_chars=120, overlap=24, min_chars=10).chunk(long)
    assert len(chunks) > 1
    # overlap: 上一块尾部应出现在下一块中
    assert chunks[0].text[-10:] in chunks[1].text


def test_metadata_propagates_and_isolated():
    meta = DocMetadata(source="case1", tags=["ml"])
    chunks = StructuralChunker(min_chars=1).chunk("内容一足够长一些。\n\n内容二足够长一些。", meta)
    assert all(c.metadata.source == "case1" for c in chunks)
    chunks[0].metadata.tags.append("mutated")
    assert chunks[1].metadata.tags == ["ml"]  # 深拷贝,互不污染


def test_chunk_ids_unique():
    chunks = StructuralChunker(min_chars=1).chunk(
        "内容一足够长一些。\n\n内容二足够长一些。", DocMetadata(source="d")
    )
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))


def test_oversize_hard_cut_no_boundary():
    # 无句末标点的超长文本也要硬切,且每块不超 max_chars
    long = "字" * 500
    chunks = StructuralChunker(max_chars=100, overlap=20, min_chars=10).chunk(long)
    assert len(chunks) > 1
    assert all(len(c.text) <= 100 for c in chunks)


def test_empty_text_returns_empty():
    assert StructuralChunker().chunk("") == []
    assert StructuralChunker().chunk("   \n\n  ") == []
