"""RAG 数据模型 — DocMetadata / Chunk / Passage / RetrievalResult。

自包含,不依赖 Stage 编排核心。照《工业级的 RAG 优化选型》:metadata tag 直接参与
检索过滤与排序;知识冲突治理靠 version / effective_date;检索结果带溯源
(Passage.metadata.source),呼应 Stage「claimed≠verified」的可信哲学。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DocMetadata(BaseModel):
    """文档 / 片段的元数据 —— 参与检索过滤(tag)与冲突治理(版本 / 时效)。"""

    source: str = ""  # 来源(文件路径 / 案例名 / URL),用于溯源
    case_id: str = ""  # 案例 id(案例库场景)
    tags: list[str] = Field(default_factory=list)  # 行业 / 产品 / 人群等标签
    version: str = ""  # 版本号,知识冲突治理
    effective_date: str = ""  # 生效日期,过时知识治理
    page: int | None = None  # 页码(PDF 提取时填充)
    extra: dict[str, Any] = Field(default_factory=dict)  # 扩展位,可演进


class Chunk(BaseModel):
    """切分后的语义单元,带 metadata;embedding 在入库时填充。"""

    id: str
    text: str
    metadata: DocMetadata = Field(default_factory=DocMetadata)
    embedding: list[float] | None = None


class Passage(BaseModel):
    """检索命中的一段证据,带分数与溯源。"""

    text: str
    score: float
    metadata: DocMetadata = Field(default_factory=DocMetadata)


class RetrievalResult(BaseModel):
    """一次检索的结果:query + 命中 passages(按分数降序)。"""

    query: str
    passages: list[Passage] = Field(default_factory=list)

    def top(self, k: int) -> list[Passage]:
        """返回前 k 条命中。"""
        return self.passages[:k]

    def sources(self) -> list[str]:
        """去重后的来源列表(按命中顺序)。"""
        seen: list[str] = []
        for p in self.passages:
            if p.metadata.source and p.metadata.source not in seen:
                seen.append(p.metadata.source)
        return seen
