"""RagPipeline 端到端 — ingest → query 检索正确主题;save/load 持久化。"""

from __future__ import annotations

import pytest

from openagents_orchestration.rag.pipeline import build_pipeline
from openagents_orchestration.rag.types import DocMetadata

ML = "机器 学习 深度 模型 训练 神经 网络 算法"
FOOD = "烹饪 食谱 烘焙 蛋糕 食材 厨房 味道"
DB = "数据 库 索引 查询 事务 存储 引擎 表"


async def test_ingest_then_query_ranks_relevant():
    pipe = build_pipeline()  # 默认 Mock embedding
    await pipe.ingest_text(ML, DocMetadata(source="ml", tags=["ml"]))
    await pipe.ingest_text(FOOD, DocMetadata(source="food", tags=["food"]))
    await pipe.ingest_text(DB, DocMetadata(source="db", tags=["db"]))
    assert len(pipe) == 3
    res = await pipe.query("机器 学习 模型", top_k=3)
    assert res.passages[0].metadata.source == "ml"
    assert res.sources()[0] == "ml"


async def test_query_filter_tags():
    pipe = build_pipeline()
    await pipe.ingest_text(ML, DocMetadata(source="ml", tags=["ml"]))
    await pipe.ingest_text(FOOD, DocMetadata(source="food", tags=["food"]))
    res = await pipe.query("机器 学习", top_k=3, filter_tags=["food"])
    assert all(p.metadata.source == "food" for p in res.passages)


async def test_ingest_empty_returns_zero():
    pipe = build_pipeline()
    assert await pipe.ingest_text("   \n  ") == 0
    assert len(pipe) == 0


async def test_save_load_preserves_retrieval(tmp_path):
    pipe = build_pipeline()
    await pipe.ingest_text(ML, DocMetadata(source="ml", tags=["ml"]))
    await pipe.ingest_text(FOOD, DocMetadata(source="food", tags=["food"]))
    f = tmp_path / "kb.json"
    pipe.save(f)
    pipe2 = build_pipeline()
    pipe2.load(f)
    res = await pipe2.query("烹饪 蛋糕", top_k=1)
    assert res.passages[0].metadata.source == "food"


async def test_ingest_file_path(tmp_path):
    doc = tmp_path / "case.md"
    doc.write_text("# 案例\n机器 学习 客户 方案 背景。", encoding="utf-8")
    pipe = build_pipeline()
    n = await pipe.ingest(str(doc), DocMetadata(tags=["ml"]))
    assert n >= 1
    res = await pipe.query("机器 学习", top_k=1)
    assert res.passages[0].metadata.source == str(doc)


async def test_ingest_missing_path_with_separator_raises():
    pipe = build_pipeline()
    with pytest.raises(FileNotFoundError, match="path not found"):
        await pipe.ingest("some/dir/missing.md", DocMetadata())
