"""Embedding 客户端 — 协议 + Mock(确定性,测试)+ Httpx(OpenAI 兼容)。

SDK 的 LLMClient 只有 generate 无 embed,故自实现。endpoint / key / model 走 env
(守 X-01),跟随 conftest 的 LLM_API_BASE / LLM_MODEL 配置先例。Mock 用确定性词袋
向量:共享 token 越多向量越近,使检索排序可测(非随机)。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Any, Protocol, runtime_checkable

import httpx

_TOKEN = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")

# env 配置(X-01:配置 / 密钥 / URL → env,禁硬编码)
ENV_EMBED_BASE = "RAG_EMBED_BASE"
ENV_EMBED_API_KEY = "RAG_EMBED_API_KEY"
ENV_EMBED_MODEL = "RAG_EMBED_MODEL"
ENV_OLLAMA_BASE = "RAG_OLLAMA_BASE"
ENV_OLLAMA_MODEL = "RAG_OLLAMA_MODEL"
_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
_DEFAULT_OLLAMA_MODEL = "nomic-embed-text"


@runtime_checkable
class EmbeddingClient(Protocol):
    """文本 → 向量。实现须确定性(同输入同输出)以便测试。"""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text) if t.strip()]


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class MockEmbeddingClient:
    """确定性词袋向量:token hash 到 dim 维,TF 加权,L2 归一化。零外部依赖。"""

    def __init__(self, dim: int = 64):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _tokenize(text):
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            h = int.from_bytes(digest[:8], "big")
            vec[h % self.dim] += 1.0
        return _normalize(vec)


class HttpxEmbeddingClient:
    """OpenAI 兼容 /v1/embeddings。httpx 调用,异常不吞(X-03)。"""

    def __init__(
        self,
        base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ):
        self.base = (base or os.environ.get(ENV_EMBED_BASE, "")).rstrip("/")
        self.api_key = api_key or os.environ.get(ENV_EMBED_API_KEY, "")
        self.model = model or os.environ.get(ENV_EMBED_MODEL, _DEFAULT_MODEL)
        self.timeout = timeout
        if not self.base:
            raise ValueError(f"embedding base not set: export {ENV_EMBED_BASE}")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    f"{self.base}/embeddings", json=payload, headers=headers
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"embedding request failed: {exc}") from exc
        data = resp.json()
        try:
            items = sorted(data["data"], key=lambda d: d["index"])
        except KeyError as exc:
            raise RuntimeError(f"embedding API response missing 'data': {list(data.keys())}") from exc
        if len(items) != len(texts):
            raise RuntimeError(
                f"embedding API returned {len(items)} vectors for {len(texts)} texts"
            )
        try:
            return [list(map(float, it["embedding"])) for it in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"malformed embedding API response: {exc}") from exc


class OllamaEmbeddingClient:
    """Ollama 原生 /api/embed。用于本地离线 embedding,避免付费远程 API。"""

    def __init__(
        self,
        base: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        batch_size: int = 16,
    ):
        self.base = (base or os.environ.get(ENV_OLLAMA_BASE, _DEFAULT_OLLAMA_BASE)).rstrip(
            "/"
        )
        self.model = model or os.environ.get(ENV_OLLAMA_MODEL, _DEFAULT_OLLAMA_MODEL)
        self.timeout = timeout
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self.batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            vectors: list[list[float]] = []
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i : i + self.batch_size]
                vectors.extend(await self._embed_batch(client, batch))
            return vectors

    async def _embed_batch(self, client, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": texts}
        try:
            resp = await client.post(f"{self.base}/api/embed", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ollama embedding request failed: {exc}") from exc
        data = resp.json()
        try:
            embeddings = data["embeddings"]
        except KeyError as exc:
            raise RuntimeError(
                f"ollama embedding response missing 'embeddings': {list(data.keys())}"
            ) from exc
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"ollama embedding returned {len(embeddings)} vectors for {len(texts)} texts"
            )
        try:
            return [list(map(float, item)) for item in embeddings]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"malformed ollama embedding response: {exc}") from exc
