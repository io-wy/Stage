# 分离式 RAG 模块(最小闭环)— 实施计划

## 目标
做一个**自包含、可独立测试**的 RAG 模块,照《工业级的 RAG 优化选型》的最小闭环。现在分离测试,之后接入 Stage 走 Agentic RAG(`rag_search` 工具 + Memory 插件)。

- **现在**:独立模块,纯 stdlib 可跑可测,不依赖 Stage 编排核心
- **之后**:包成 `RagSearchTool`(ToolPlugin)/ RAG Memory 插件,接 `TOOL_REGISTRY`(下一步,不在本次)

## 物理位置
`src/openagents_orchestration/rag/`(新子包)。放 repo 里但**逻辑独立**——不 import `core/`/`projects/`,自包含;后续"接入 Stage"只加一层工具包装,不搬代码。测试平铺 `tests/test_rag_*.py`(跟随现有风格)。

## 模块结构
```
src/openagents_orchestration/rag/
├── __init__.py       # 公共 API: RagPipeline, Chunk, Passage, build_pipeline
├── types.py          # pydantic: DocMetadata / Chunk / Passage / RetrievalResult
├── chunking.py       # StructuralChunker: 结构感知切分(保语义单元)+ metadata
├── embedding.py      # EmbeddingClient 协议 + Httpx + Mock(确定性,测试用)
├── vectorstore.py    # VectorStore 接口 + InMemoryCosineStore(stdlib 余弦 + JSON)
├── extract.py        # 版式探测 + TextExtractor 接口 + PlainText + Pdf(可选 pypdf)
├── retriever.py      # Retriever: query → embed → search → top_k(带溯源)
└── pipeline.py       # RagPipeline: ingest(建库)+ query(检索)+ save/load

tests/test_rag_{chunking,embedding,vectorstore,retriever,pipeline}.py
```

## 核心数据模型(types.py,pydantic)
- `DocMetadata`: source / case_id / tags / version / effective_date / page —— 接参考文档「metadata tag 参与检索」+「知识冲突治理(版本/时效)」
- `Chunk`: id / text / metadata / embedding?
- `Passage`: text / score / metadata —— 检索结果带**溯源**(接 Stage「claimed≠verified」哲学)
- `RetrievalResult`: query / passages

## 关键设计决策
1. **embedding**:`EmbeddingClient` 协议(`embed(texts) -> vectors`)。
   - `HttpxEmbeddingClient`:httpx 调 OpenAI 兼容 `/v1/embeddings`,env 配置 `RAG_EMBED_BASE` / `RAG_EMBED_API_KEY` / `RAG_EMBED_MODEL`(跟随 conftest 的 env 先例,守 X-01)。
   - `MockEmbeddingClient`:确定性词袋向量(共享 token 越多越近)——测试零外部依赖,且**语义可测**(能验证检索排序正确性,非随机)。
2. **vectorstore**:`InMemoryCosineStore`,stdlib 余弦(`math.sqrt`,不引 numpy),JSON 持久化;支持 `filter_tags`(为「定向检索/双路」预留)。
3. **chunking**:`StructuralChunker`——先按 Markdown 标题/空行分段(保语义单元),超长按句子 + overlap 细分,短段合并;每 chunk 带 metadata。
4. **extract**:版式探测 + 可插拔后端(弹性 ingestion)。`PlainTextExtractor`(.txt/.md,stdlib 立即可测)+ `PdfTextExtractor`(pypdf,**import 失败降级**抛清晰错误);无文本层 → 提示需 OCR(占位,不实现)。

## 依赖策略(X-05)
- 核心:**零新增**——stdlib + pydantic(已有)+ httpx(已有)
- pypdf:**可选**,仅 PDF 直提需要,未装则该后端降级报错;现在不装,真要看 PDF 时再评估引入并告知

## 测试策略(X-06:mock 外部依赖)
- 全部用 `MockEmbeddingClient`,**不打真实 embedding API**;`HttpxEmbeddingClient` 用 mock httpx
- chunking:语义切分 / overlap / metadata 传递
- vectorstore:余弦正确性 / top_k 排序 / tag 过滤 / save-load 持久化
- retriever + pipeline:端到端——ingest 假文档 → query → 断言检索回正确 chunk(借 MockEmbedding 的语义特性)
- 纯 stdlib 可跑

## 范围边界
- **做(最小闭环)**:ingest(解析+切分+tag)→ embed → store → retrieve(带溯源)
- **不做(留扩展点,等有数据)**:混合检索(BM25)、rerank、query 改写、GraphRAG、评测——在 retriever/vectorstore 留 hook,标 `# TODO(待数据)`,不实现(照参考文档「重武器等飞轮转起来」)
- **现在不做**:接 Stage 的 `RagSearchTool` / Memory 插件(下一步)

## 验证
- `pytest tests/test_rag_*.py` 全绿
- `ruff check --no-cache`(避开 stale cache 幻影错)
- 端到端 smoke:ingest 假文档 → query → 检索回正确 chunk

## 流程(项目宪法:功能 → 测试 → 审查 → 提交)
1. 功能实现(types → chunking → embedding → vectorstore → extract → retriever → pipeline)
2. 单元测试(5 个 test_rag_*.py)
3. 自审 + 多模型对抗审查(改动 ≥5 文件 / ≥200 行,触发)
4. 原子 commit(不 push,除非你要)
