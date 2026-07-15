"""结构感知切分 — 保语义单元,带 metadata。

照《工业级的 RAG 优化选型》:chunk 不是越小越好也不是越大越好,每个 chunk 保持
相对完整的语义单元;保留文档结构,避免太碎(语义不完整)/太长(召回不精准)。
策略:先按 Markdown 标题 / 空行分段,超长按句子 + overlap 细分,短段合并。
"""

from __future__ import annotations

import re

from openagents_orchestration.rag.types import Chunk, DocMetadata

# 空行(段落分隔)
_PARA_SPLIT = re.compile(r"\n\s*\n")


class StructuralChunker:
    """结构感知切分器:标题 / 段落优先,超长再细分,短段合并。"""

    def __init__(self, max_chars: int = 800, overlap: int = 120, min_chars: int = 120):
        if not 0 <= overlap < max_chars:
            raise ValueError("overlap must satisfy 0 <= overlap < max_chars")
        self.max_chars = max_chars
        self.overlap = overlap
        self.min_chars = min_chars

    def chunk(self, text: str, metadata: DocMetadata | None = None) -> list[Chunk]:
        """把文本切成 Chunk 列表,每块继承 metadata 的独立副本。"""
        meta = metadata or DocMetadata()
        blocks = self._split_blocks(text)
        merged = self._merge_small(blocks)
        chunks: list[Chunk] = []
        for block in merged:
            for piece in self._split_oversize(block):
                piece = piece.strip()
                if piece:
                    chunks.append(self._make_chunk(piece, meta, len(chunks)))
        return chunks

    # ---- 分段:标题切大段,段内按空行分段 -------------------------
    def _split_blocks(self, text: str) -> list[str]:
        blocks: list[str] = []
        # 以标题为边界切大段(标题并入该大段开头)
        for part in re.split(r"(?m)(?=^#{1,6}\s)", text):
            part = part.strip()
            if not part:
                continue
            for para in _PARA_SPLIT.split(part):
                para = para.strip()
                if para:
                    blocks.append(para)
        return blocks

    # ---- 合并过短段落 -------------------------
    def _merge_small(self, blocks: list[str]) -> list[str]:
        merged: list[str] = []
        buf = ""
        for b in blocks:
            is_heading = b.lstrip().startswith("#")
            if not buf:
                buf = b
            elif is_heading:
                # 标题块是独立语义单元,不并入前一块(避免跨标题合并)
                merged.append(buf)
                buf = b
            elif len(buf) < self.min_chars and len(buf) + len(b) + 2 <= self.max_chars:
                buf = f"{buf}\n\n{b}"
            else:
                merged.append(buf)
                buf = b
        if buf:
            merged.append(buf)
        return merged

    # ---- 超长段细分:字符窗口 + 句末边界对齐 + overlap -------------------------
    def _split_oversize(self, block: str) -> list[str]:
        if len(block) <= self.max_chars:
            return [block]
        pieces: list[str] = []
        start = 0
        n = len(block)
        while start < n:
            end = min(start + self.max_chars, n)
            if end < n:
                # 尽量在句末边界切,不硬切句子(覆盖中英文句末标点 / 换行)
                boundary = max(block.rfind(ch, start, end) for ch in "。!?;；\n")
                if boundary > start:
                    end = boundary + 1
            pieces.append(block[start:end])
            # overlap 前进,但至少 +1 防死循环(超长无标点也能推进)
            start = max(end - self.overlap, start + 1)
        return [p for p in pieces if p.strip()]

    # ---- 构造 Chunk -------------------------
    @staticmethod
    def _make_chunk(text: str, meta: DocMetadata, idx: int) -> Chunk:
        cid = f"{meta.source or 'doc'}#{idx}"
        return Chunk(id=cid, text=text, metadata=meta.model_copy(deep=True))
