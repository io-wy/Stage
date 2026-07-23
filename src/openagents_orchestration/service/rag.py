"""RAG application services and governed backend adapters."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.models import CaseAuditEvent
from openagents_orchestration.control.router import GovernancePlan
from openagents_orchestration.handler.http.schemas import (
    EmbeddingMode,
    RagPassageResponse,
    RagQueryRequest,
    RagQueryResponse,
)
from openagents_orchestration.rag import (
    DocMetadata,
    MockEmbeddingClient,
    OllamaEmbeddingClient,
    build_pipeline,
)
from openagents_orchestration.service.common import resolve_path, resolve_wiki_path
from openagents_orchestration.service.settings import DEFAULT_OUTPUT_ROOT


async def query_rag(request: RagQueryRequest) -> RagQueryResponse:
    wiki_path = resolve_wiki_path(request.wiki_path)
    kb_path = resolve_path(
        request.kb_path,
        default_kb_path(wiki_path, request.embedding),
    )
    pipe = build_pipeline(embedding=_embedding(request.embedding))
    if kb_path.exists():
        pipe.load(kb_path)
    else:
        await _ingest_wiki(pipe, wiki_path)
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        pipe.save(kb_path)

    result = await pipe.query(request.question, top_k=request.top_k)
    return RagQueryResponse(
        question=request.question,
        wiki_path=str(wiki_path),
        embedding=request.embedding,
        kb_path=str(kb_path),
        chunk_count=len(pipe),
        passages=[
            RagPassageResponse(
                source=passage.metadata.source,
                score=passage.score,
                text=passage.text,
                tags=list(passage.metadata.tags),
                score_breakdown=passage.metadata.extra.get("score_breakdown", {}),
            )
            for passage in result.passages
        ],
    )


class RagGovernanceBackend:
    """Case backend that executes the RAG node inside the governance run."""

    execution_mode = "rag_governed"

    def __init__(
        self,
        *,
        wiki_path: Path,
        embedding: EmbeddingMode,
        kb_path: Path,
        top_k: int,
    ):
        self.wiki_path = wiki_path
        self.embedding = embedding
        self.kb_path = kb_path
        self.top_k = top_k
        self.last_rag_log: dict[str, Any] = {}

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: GovernancePlan,
        audit_store: AuditStore,
    ) -> dict[str, Any]:
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="tool_invoked",
                payload={
                    "backend": "rag",
                    "execution_mode": self.execution_mode,
                    "route": route_plan.to_dict(),
                    "wiki_path": str(self.wiki_path),
                    "kb_path": str(self.kb_path),
                    "embedding": self.embedding,
                },
            )
        )
        if "rag_retrieval" not in route_plan.backends:
            self.last_rag_log = {"skipped": True, "reason": "route did not request RAG"}
            return {
                "family": _family_for_route(route_plan),
                "closed": False,
                "answer": "该请求没有路由到 RAG，需要后续执行节点处理。",
                "actions": ["create_handoff"],
                "evidence": [],
                "human_questions": [
                    "该请求需要由非 RAG 执行节点继续处理，请确认执行人或工具。"
                ],
            }

        rag_log = asyncio.run(
            _query_rag_log(
                wiki_path=self.wiki_path,
                embedding=self.embedding,
                kb_path=self.kb_path,
                question=prompt,
                top_k=self.top_k,
            )
        )
        self.last_rag_log = rag_log
        answer = rag_log.get("answer", {})
        answered = answer.get("status") == "answered"
        evidence = [
            {
                "file": passage.get("source", ""),
                "summary": passage.get("snippet", ""),
                "rank": passage.get("rank"),
                "score": passage.get("score"),
                "tags": passage.get("tags", []),
                "score_breakdown": passage.get("score_breakdown", {}),
            }
            for passage in rag_log.get("retrieval", {}).get("passages", [])
        ]
        needs_human = bool(route_plan.needs_human or "human_channel" in route_plan.backends)
        return {
            "family": _family_for_route(route_plan),
            "closed": bool(answered and not needs_human),
            "answer": answer.get("answer_text")
            or _public_rag_refusal(answer.get("refusal_reason", ""))
            or "没有检索到足够证据。",
            "actions": ["answer_user"] if answered and not needs_human else ["create_handoff"],
            "evidence": evidence,
            "human_questions": _route_human_questions(route_plan) if needs_human else [],
            "failure_mode": None if answered else "missing_required_information",
            "confidence": route_plan.confidence,
        }


async def _query_rag_log(
    *,
    wiki_path: Path,
    embedding: EmbeddingMode,
    kb_path: Path,
    question: str,
    top_k: int,
) -> dict[str, Any]:
    pipe = build_pipeline(embedding=_embedding(embedding))
    if kb_path.exists():
        pipe.load(kb_path)
    else:
        await _ingest_wiki(pipe, wiki_path)
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        pipe.save(kb_path)
    log = await pipe.answer_with_log(
        question,
        top_k=top_k,
        use_route_classifier=False,
    )
    payload = log.model_dump(mode="json")
    payload["kb_path"] = str(kb_path)
    payload["wiki_path"] = str(wiki_path)
    payload["chunk_count"] = len(pipe)
    payload["embedding"] = embedding
    return payload


async def _ingest_wiki(pipe: Any, wiki_path: Path) -> None:
    root = wiki_path if wiki_path.is_dir() else wiki_path.parent
    for path in _iter_wiki_files(wiki_path):
        source = path.relative_to(root).as_posix() if path != root else path.name
        await pipe.ingest(path, DocMetadata(source=source, tags=["wiki"]))


def _iter_wiki_files(wiki_path: Path) -> list[Path]:
    if wiki_path.is_file():
        return [wiki_path]
    files: list[Path] = []
    for path in wiki_path.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            files.append(path)
    return sorted(files)


def _embedding(kind: EmbeddingMode):
    if kind == "mock":
        return MockEmbeddingClient()
    return OllamaEmbeddingClient(timeout=180, batch_size=1)


def _family_for_route(route_plan: GovernancePlan) -> str:
    if "human_channel" in route_plan.backends and "approval" in route_plan.business_process:
        return "approval"
    if "human_channel" in route_plan.backends:
        return "ticket"
    return "knowledge"


def _route_human_questions(route_plan: GovernancePlan) -> list[str]:
    if "approval" in route_plan.business_process:
        return ["请提供审批人、目标资源、权限范围和工单号。"]
    return ["请补充人工确认信息后再闭环。"]


def _public_rag_refusal(refusal_reason: str) -> str:
    if refusal_reason == "permission denied for retrieved evidence":
        return "检索到了受限证据，不能在公开回复中直接展开；已转人工确认或补充公开依据。"
    if refusal_reason == "no retrieval evidence available":
        return "没有检索到足够公开证据，不能直接闭环。"
    return refusal_reason


def default_kb_path(wiki_path: Path, embedding: EmbeddingMode) -> Path:
    digest = hashlib.sha1(str(wiki_path).encode("utf-8")).hexdigest()[:12]
    return DEFAULT_OUTPUT_ROOT / "cache" / f"rag-{embedding}-{digest}.json"
