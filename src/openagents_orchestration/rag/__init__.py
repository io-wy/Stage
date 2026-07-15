"""分离式 RAG 模块 — 最小闭环(ingest → embed → store → retrieve)。

自包含,可独立测试,不依赖 Stage 编排核心;之后包成 RagSearchTool(ToolPlugin)/
RAG Memory 插件接入 Stage 走 Agentic RAG。照《工业级的 RAG 优化选型》:重武器
(混合检索 / rerank / query 改写)留扩展点,等有真实数据与 badcase 后再加。
"""

from openagents_orchestration.rag.chunking import StructuralChunker
from openagents_orchestration.rag.embedding import (
    EmbeddingClient,
    HttpxEmbeddingClient,
    MockEmbeddingClient,
)
from openagents_orchestration.rag.extract import (
    PdfTextExtractor,
    PlainTextExtractor,
    TextExtractor,
    extract,
    extract_text,
)
from openagents_orchestration.rag.pipeline import RagPipeline, build_pipeline
from openagents_orchestration.rag.retriever import Retriever
from openagents_orchestration.rag.types import (
    Chunk,
    DocMetadata,
    Passage,
    RetrievalResult,
)
from openagents_orchestration.rag.vectorstore import InMemoryCosineStore, VectorStore

__all__ = [
    "Chunk",
    "DocMetadata",
    "EmbeddingClient",
    "HttpxEmbeddingClient",
    "InMemoryCosineStore",
    "MockEmbeddingClient",
    "Passage",
    "PdfTextExtractor",
    "PlainTextExtractor",
    "RagPipeline",
    "RetrievalResult",
    "Retriever",
    "StructuralChunker",
    "TextExtractor",
    "VectorStore",
    "build_pipeline",
    "extract",
    "extract_text",
]
