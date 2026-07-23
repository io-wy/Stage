"""分离式 RAG 模块 — 最小闭环(ingest → embed → store → retrieve)。

自包含,可独立测试,不依赖 Stage 编排核心;之后包成 RagSearchTool(ToolPlugin)/
RAG Memory 插件接入 Stage 走 Agentic RAG。照《工业级的 RAG 优化选型》:重武器
(混合检索 / rerank / query 改写)留扩展点,等有真实数据与 badcase 后再加。
"""

from openagents_orchestration.rag.answer import EvidenceOnlyAnswerer
from openagents_orchestration.rag.badcase import (
    BadcaseRecord,
    BadcaseTracePassage,
    badcases_to_eval_cases,
    dump_badcases_jsonl,
    load_badcases_jsonl,
)
from openagents_orchestration.rag.chunking import StructuralChunker
from openagents_orchestration.rag.embedding import (
    EmbeddingClient,
    HttpxEmbeddingClient,
    MockEmbeddingClient,
    OllamaEmbeddingClient,
)
from openagents_orchestration.rag.eval import (
    RetrievalEvalCase,
    RetrievalEvalItem,
    RetrievalEvalSummary,
    RetrievalTracePassage,
    evaluate_retrieval,
)
from openagents_orchestration.rag.extract import (
    DocxTextExtractor,
    PdfTextExtractor,
    PlainTextExtractor,
    TextExtractor,
    XlsxTextExtractor,
    extract,
    extract_text,
)
from openagents_orchestration.rag.pipeline import RagPipeline, build_pipeline
from openagents_orchestration.rag.retriever import Retriever
from openagents_orchestration.rag.rewrite import (
    ControlledQueryRewriter,
    QueryRewriteDecision,
)
from openagents_orchestration.rag.route import RouteClassifier, RouteDecision
from openagents_orchestration.rag.runlog import (
    RagAnswerCitationLog,
    RagAnswerLog,
    RagQueryRunLog,
    RagRetrievalLog,
    RagRetrievalPassageLog,
)
from openagents_orchestration.rag.types import (
    Chunk,
    DocMetadata,
    Passage,
    RetrievalResult,
)
from openagents_orchestration.rag.vectorstore import InMemoryCosineStore, VectorStore

__all__ = [
    "BadcaseRecord",
    "BadcaseTracePassage",
    "Chunk",
    "ControlledQueryRewriter",
    "DocMetadata",
    "DocxTextExtractor",
    "EmbeddingClient",
    "EvidenceOnlyAnswerer",
    "HttpxEmbeddingClient",
    "InMemoryCosineStore",
    "MockEmbeddingClient",
    "OllamaEmbeddingClient",
    "Passage",
    "PdfTextExtractor",
    "PlainTextExtractor",
    "QueryRewriteDecision",
    "RagAnswerCitationLog",
    "RagAnswerLog",
    "RagPipeline",
    "RagQueryRunLog",
    "RagRetrievalLog",
    "RagRetrievalPassageLog",
    "RetrievalEvalCase",
    "RetrievalEvalItem",
    "RetrievalEvalSummary",
    "RetrievalResult",
    "RetrievalTracePassage",
    "Retriever",
    "RouteClassifier",
    "RouteDecision",
    "StructuralChunker",
    "TextExtractor",
    "VectorStore",
    "XlsxTextExtractor",
    "badcases_to_eval_cases",
    "build_pipeline",
    "dump_badcases_jsonl",
    "evaluate_retrieval",
    "extract",
    "extract_text",
    "load_badcases_jsonl",
]
