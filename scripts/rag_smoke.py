"""分离式 RAG 端到端 smoke — ingest 假案例库 → 语义检索 → 持久化往返。

跑法: PYTHONPATH=src .venv/bin/python scripts/rag_smoke.py
纯 stdlib + Mock embedding,零外部依赖,不打真实 API。验证整条最小闭环链路。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from openagents_orchestration.rag import DocMetadata, build_pipeline

# 空格分隔中文关键词(Mock 词袋 embedding 需要;真实 embedding 无需)
CASES = [
    ("case_ml", "机器 学习 深度 模型 训练 神经 网络 梯度 反向 传播", ["ml", "算法"]),
    ("case_food", "烘焙 蛋糕 食谱 烤箱 温度 发酵 面粉 食材", ["food", "烹饪"]),
    ("case_db", "数据 库 索引 B树 查询 优化 事务 隔离 级别", ["db", "后端"]),
    ("case_k8s", "容器 编排 集群 调度 镜像 部署 弹性 伸缩 服务", ["k8s", "运维"]),
]

QUERIES = [
    "机器 学习 模型 怎么 训练",
    "怎么 做 蛋糕 烘焙",
    "数据 库 查询 慢 怎么 优化",
]


async def main() -> None:
    pipe = build_pipeline()
    for cid, text, tags in CASES:
        n = await pipe.ingest_text(text, DocMetadata(source=cid, tags=tags))
        print(f"ingest {cid}: {n} chunk, tags={tags}")
    print(f"\n库大小: {len(pipe)} chunks\n")

    for q in QUERIES:
        res = await pipe.query(q, top_k=2)
        print(f"Q: {q}")
        for p in res.passages:
            print(f"  [{p.score:.3f}] {p.metadata.source} | {p.text[:28]}…")
        print()

    # 持久化往返:save → load → 再检索
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "kb.json"
        pipe.save(f)
        pipe2 = build_pipeline()
        pipe2.load(f)
        res = await pipe2.query("容器 部署 弹性", top_k=1)
        print(f"持久化往返后 Q『容器 部署 弹性』→ top1 = {res.passages[0].metadata.source}")


if __name__ == "__main__":
    asyncio.run(main())
