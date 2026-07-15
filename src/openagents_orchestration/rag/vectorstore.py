"""向量存储 — 接口 + InMemoryCosineStore(stdlib 余弦 + JSON 持久化)。

小库用纯 Python 余弦即可(不引 numpy);大库后续换实现,接口已抽象。支持 tag 过滤
(为「定向检索 / 双路并行」预留)。持久化用临时文件 + 原子替换,避免写半截;
失败时清理 .tmp。
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from openagents_orchestration.rag.types import Chunk, Passage


@runtime_checkable
class VectorStore(Protocol):
    """向量库的公共契约:入库、检索、持久化。"""

    def add(self, chunks: list[Chunk]) -> None: ...

    def search(
        self, vector: list[float], top_k: int = 5, filter_tags: list[str] | None = None
    ) -> list[Passage]: ...

    def save(self, path: str | Path) -> None: ...

    def load(self, path: str | Path) -> None: ...

    def __len__(self) -> int: ...


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class InMemoryCosineStore:
    """内存向量库:stdlib 余弦 + tag 过滤 + JSON 原子持久化。"""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._dim: int | None = None
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._chunks)

    def add(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for c in chunks:
                if c.embedding is None:
                    raise ValueError(f"chunk {c.id!r} has no embedding; embed before add")
                dim = len(c.embedding)
                if self._dim is None:
                    self._dim = dim
                elif self._dim != dim:
                    raise ValueError(
                        f"embedding dim mismatch: expected {self._dim}, got {dim}"
                    )
            self._chunks.extend(chunks)

    def search(
        self, vector: list[float], top_k: int = 5, filter_tags: list[str] | None = None
    ) -> list[Passage]:
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        with self._lock:
            if self._dim is not None and len(vector) != self._dim:
                raise ValueError(
                    f"query vector dim {len(vector)} != store dim {self._dim}"
                )
            want = set(filter_tags) if filter_tags else None
            scored: list[Passage] = []
            for c in self._chunks:
                if want and not (want & set(c.metadata.tags)):
                    continue
                score = _cosine(vector, c.embedding or [])
                scored.append(
                    Passage(text=c.text, score=score, metadata=c.metadata.model_copy(deep=True))
                )
            scored.sort(key=lambda p: p.score, reverse=True)
            return scored[:top_k]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = [c.model_dump() for c in self._chunks]
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)  # 原子替换,避免半截文件
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def load(self, path: str | Path) -> None:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        chunks = [Chunk.model_validate(d) for d in data]
        with self._lock:
            self._chunks = chunks
            self._dim = len(chunks[0].embedding) if chunks else None
