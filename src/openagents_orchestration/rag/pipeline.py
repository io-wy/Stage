"""RagPipeline — extract / chunk / embed / store / retrieve 串成最小闭环。

ingest(建库): extract → chunk → embed → store.add
query(检索):  Retriever.query → RetrievalResult(带溯源)
save / load:   向量库持久化

自包含,不依赖 Stage 编排核心;之后包成 RagSearchTool(ToolPlugin)/ Memory 插件接入。
"""

from __future__ import annotations

from pathlib import Path
from time import time

from openagents_orchestration.rag.answer import EvidenceOnlyAnswerer
from openagents_orchestration.rag.chunking import StructuralChunker
from openagents_orchestration.rag.embedding import EmbeddingClient, MockEmbeddingClient
from openagents_orchestration.rag.extract import TextExtractor
from openagents_orchestration.rag.extract import extract as _extract_file
from openagents_orchestration.rag.permissions import apply_permission_tags
from openagents_orchestration.rag.retriever import Retriever
from openagents_orchestration.rag.rewrite import ControlledQueryRewriter
from openagents_orchestration.rag.route import RouteClassifier, RouteDecision
from openagents_orchestration.rag.runlog import RagQueryRunLog, make_retrieval_log
from openagents_orchestration.rag.types import Chunk, DocMetadata, RetrievalResult
from openagents_orchestration.rag.vectorstore import InMemoryCosineStore, VectorStore

_MAX_PATH_LEN = 240


class RagPipeline:
    """ingest + query 的最小闭环。"""

    def __init__(
        self,
        extractor: TextExtractor | None,
        chunker: StructuralChunker,
        embedding: EmbeddingClient,
        store: VectorStore,
        route_classifier: RouteClassifier | None = None,
    ):
        self._extractor = extractor
        self._chunker = chunker
        self._embedding = embedding
        self._store = store
        self._retriever = Retriever(embedding, store)
        self._route_classifier = route_classifier or RouteClassifier()
        self._query_rewriter = ControlledQueryRewriter()
        self._answerer = EvidenceOnlyAnswerer()

    def __len__(self) -> int:
        return len(self._store)

    async def ingest(self, source: str | Path, metadata: DocMetadata | None = None) -> int:
        """入库:source 为文件路径或纯文本。返回入库 chunk 数。"""
        meta = metadata or DocMetadata()
        if _looks_like_path(source):
            meta = meta.model_copy(deep=True)
            if not meta.source:
                meta.source = str(source)
            pages = _extract_file(source, self._extractor)
        elif _looks_intended_as_path(source):
            raise FileNotFoundError(
                f"path not found: {source}; use ingest_text(...) for literal text"
            )
        else:
            pages = [(None, str(source))]
        return await self._ingest_pages(pages, meta)

    async def ingest_text(self, text: str, metadata: DocMetadata | None = None) -> int:
        """直接入库纯文本(不走文件)。"""
        return await self._ingest_pages([(None, text)], metadata or DocMetadata())

    async def _ingest_pages(self, pages: list, meta: DocMetadata) -> int:
        chunks: list[Chunk] = []
        for page_no, text in pages:
            if not text or not text.strip():
                continue
            page_meta = meta.model_copy(deep=True)
            page_meta.page = page_no
            page_chunks = self._chunker.chunk(text, page_meta)
            for chunk in page_chunks:
                chunk.metadata.tags = apply_permission_tags(
                    chunk.metadata.tags,
                    chunk.metadata.source,
                    chunk.text,
                )
            chunks.extend(page_chunks)
        if not chunks:
            return 0
        vectors = await self._embedding.embed([c.text for c in chunks])
        for c, v in zip(chunks, vectors, strict=True):
            c.embedding = v
        self._store.add(chunks)
        return len(chunks)

    async def query(
        self,
        text: str,
        top_k: int = 5,
        filter_tags: list[str] | None = None,
        required_tags: list[str] | None = None,
    ) -> RetrievalResult:
        return await self._retriever.query(text, top_k, filter_tags, required_tags)

    async def query_with_log(
        self,
        text: str,
        top_k: int = 5,
        filter_tags: list[str] | None = None,
        required_tags: list[str] | None = None,
        use_route_classifier: bool = True,
        use_query_rewrite: bool = True,
    ) -> tuple[RetrievalResult, RagQueryRunLog]:
        started_at = time()
        route: RouteDecision | None = None
        effective_filter_tags = filter_tags
        if use_route_classifier and not effective_filter_tags:
            route = self._route_classifier.classify(text)
            effective_filter_tags = route.filter_tags or None
        rewrite = (
            self._query_rewriter.rewrite(text, route)
            if use_query_rewrite
            else None
        )
        retrieval_query = rewrite.rewritten_query if rewrite is not None else text
        result = await self.query(
            retrieval_query,
            top_k,
            effective_filter_tags,
            required_tags,
        )
        log = RagQueryRunLog(
            query=text,
            started_at=started_at,
            finished_at=time(),
            route=route,
            rewrite=rewrite,
            retrieval=make_retrieval_log(
                result,
                top_k=top_k,
                filter_tags=effective_filter_tags,
                required_tags=required_tags,
            ),
        )
        return result, log

    async def answer_with_log(
        self,
        text: str,
        top_k: int = 5,
        filter_tags: list[str] | None = None,
        required_tags: list[str] | None = None,
        allowed_permission_tags: tuple[str, ...] | list[str] | None = None,
        use_route_classifier: bool = True,
        use_query_rewrite: bool = True,
    ) -> RagQueryRunLog:
        result, log = await self.query_with_log(
            text,
            top_k=top_k,
            filter_tags=filter_tags,
            required_tags=required_tags,
            use_route_classifier=use_route_classifier,
            use_query_rewrite=use_query_rewrite,
        )
        log.answer = self._answerer.answer(
            result,
            allowed_permission_tags=allowed_permission_tags,
        )
        log.finished_at = time()
        return log

    def save(self, path: str | Path) -> None:
        self._store.save(path)

    def load(self, path: str | Path) -> None:
        self._store.load(path)


def _looks_like_path(source: str | Path) -> bool:
    if isinstance(source, Path):
        return True
    if not isinstance(source, str):
        return False
    if "\n" in source or len(source) > _MAX_PATH_LEN:
        return False
    try:
        return Path(source).is_file()
    except (OSError, ValueError):
        return False


def _looks_intended_as_path(source: str | Path) -> bool:
    """判断用户意图是路径(即使该路径当前不存在):含路径分隔符或 ~/ 前缀。"""
    if isinstance(source, Path):
        return True
    if not isinstance(source, str):
        return False
    return "/" in source or "\\" in source or source.startswith("~/")


def build_pipeline(
    embedding: EmbeddingClient | None = None,
    extractor: TextExtractor | None = None,
    chunker: StructuralChunker | None = None,
    store: VectorStore | None = None,
    route_classifier: RouteClassifier | None = None,
) -> RagPipeline:
    """工厂:默认 Mock embedding(分离测零外部依赖)+ 内存余弦库。生产显式传 Httpx。"""
    return RagPipeline(
        extractor=extractor,
        chunker=chunker or StructuralChunker(),
        embedding=embedding or MockEmbeddingClient(),
        store=store if store is not None else InMemoryCosineStore(),
        route_classifier=route_classifier,
    )
