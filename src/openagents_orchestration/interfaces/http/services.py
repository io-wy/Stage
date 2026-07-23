"""Application services for the Stage local HTTP console."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from time import time
from typing import Any

from openagents_orchestration.governance.audit import AuditStore
from openagents_orchestration.governance.domain import GovernanceDomainResolver
from openagents_orchestration.governance.feedback import (
    CaseFeedbackRecord,
    write_feedback_artifacts,
)
from openagents_orchestration.governance.intent_llm import (
    build_governance_intent_classifier,
)
from openagents_orchestration.governance.models import CaseAuditEvent
from openagents_orchestration.governance.pipeline import StageGovernancePipeline
from openagents_orchestration.governance.router import GovernancePlan
from openagents_orchestration.interfaces.http.schemas import (
    AuditEventResponse,
    DemoCaseSummary,
    EmbeddingMode,
    FeedbackRequest,
    FeedbackResponse,
    RagPassageResponse,
    RagQueryRequest,
    RagQueryResponse,
    RunDemoCaseRequest,
    RunDemoCaseResponse,
    RunDetailResponse,
    RunGovernanceRequest,
    RunGovernanceResponse,
    RunHistoryItem,
)
from openagents_orchestration.rag import (
    DocMetadata,
    MockEmbeddingClient,
    OllamaEmbeddingClient,
    build_pipeline,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_EVALS_JSON = REPO_ROOT / "skills" / "case-handling-baseline" / "evals" / "evals.json"
DEFAULT_BASELINE_WORKSPACE = (
    REPO_ROOT / "skills" / "case-handling-baseline-workspace" / "hard-v2-baseline"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "docs" / "reports" / "stage-web-console"
DEFAULT_FEEDBACK_ROOT = REPO_ROOT / "docs" / "reports" / "stage-feedback"
DEFAULT_WIKI_PATH = Path("/Users/io/Downloads/wiki/SAST 设施指南")
AUDIT_STAGE_ORDER = {
    "intent_classified": 10,
    "domain_resolved": 20,
    "governance_frame_built": 30,
    "backend_planned": 40,
    "permission_preflight_checked": 50,
    "tool_invoked": 60,
    "permission_postcheck_checked": 70,
    "evidence_added": 80,
    "claim_trace_built": 90,
    "safety_checked": 100,
    "verification_checked": 110,
    "closure_checked": 120,
    "case_closed": 130,
    "case_blocked": 130,
    "human_handoff_created": 140,
}


def health_payload() -> dict[str, str]:
    return {
        "status": "ok",
        "default_output_root": str(DEFAULT_OUTPUT_ROOT),
        "default_feedback_root": str(DEFAULT_FEEDBACK_ROOT),
    }


def list_demo_cases(evals_json: str | None = None) -> list[DemoCaseSummary]:
    from eval.case_handling.stage_governance_runner import load_hard_v2_eval_specs

    specs = load_hard_v2_eval_specs(_resolve_path(evals_json, DEFAULT_EVALS_JSON))
    return [
        DemoCaseSummary(
            eval_id=spec.eval_id,
            name=spec.eval_name,
            family=spec.family,
            business_process=spec.business_process,
            expected_closed=spec.expected_closed,
            prompt=spec.prompt,
        )
        for spec in specs
    ]


def run_demo_case(request: RunDemoCaseRequest) -> RunDemoCaseResponse:
    from eval.case_handling.stage_governance_runner import (
        load_hard_v2_eval_specs,
        run_stage_governance_eval,
    )

    evals_json = _resolve_path(request.evals_json, DEFAULT_EVALS_JSON)
    baseline_workspace = _resolve_path(
        request.baseline_workspace,
        DEFAULT_BASELINE_WORKSPACE,
    )
    output_root = _resolve_path(request.output_root, DEFAULT_OUTPUT_ROOT)
    specs = load_hard_v2_eval_specs(evals_json, eval_ids=[request.eval_id])
    if not specs:
        raise ValueError(f"demo case not found: {request.eval_id}")

    result = run_stage_governance_eval(
        specs[0],
        baseline_workspace=baseline_workspace,
        output_root=output_root,
    )
    case_result = result.governed_case_result
    governance = result.governance
    return RunDemoCaseResponse(
        eval_id=result.eval_id,
        eval_name=result.eval_name,
        closed=bool(case_result.get("closed")),
        failure_mode=case_result.get("failure_mode"),
        route=governance.get("route", {}),
        permissions=governance.get("permissions", {}),
        safety=governance.get("safety", {}),
        closure=governance.get("closure", {}),
        evidence=governance.get("public_evidence", []),
        claim_trace=governance.get("claim_trace", []),
        audit_events=governance.get("audit_events", []),
        artifact_paths={
            "governance": result.governance_path,
            "case_result": result.case_result_path,
            "audit": result.audit_path,
        },
        governance=governance,
        case_result=case_result,
    )


def list_run_history(output_root: str | None = None) -> list[RunHistoryItem]:
    root = _resolve_path(output_root, DEFAULT_OUTPUT_ROOT)
    if not root.exists():
        return []
    items: list[RunHistoryItem] = []
    for governance_path in sorted(
        root.rglob("governance.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        try:
            governance = _read_json(governance_path)
        except json.JSONDecodeError:
            continue
        case_result_path = Path(str(governance.get("case_result_path", "")))
        if case_result_path and not case_result_path.is_absolute():
            case_result_path = REPO_ROOT / case_result_path
        case_result = _read_json(case_result_path) if case_result_path.exists() else {}
        route = governance.get("route", {})
        domain = governance.get("domain", {})
        run_key = _run_key(governance_path)
        items.append(
            RunHistoryItem(
                run_key=run_key,
                case_id=str(governance.get("case_id", "")),
                run_id=str(governance.get("run_id", "")),
                eval_name=governance_path.parent.name,
                closed=case_result.get("closed"),
                failure_mode=case_result.get("failure_mode"),
                business_process=str(domain.get("business_process", "")),
                route_backends=list(route.get("backends", [])),
                governance_path=str(governance_path),
                case_result_path=str(case_result_path) if case_result_path else "",
                audit_path=str(governance.get("audit_path", "")),
                created_at=governance_path.stat().st_mtime,
            )
        )
    return items


def get_run_detail(run_key: str, output_root: str | None = None) -> RunDetailResponse:
    governance_path = _find_run_governance_path(run_key, output_root)
    governance = _read_json(governance_path)
    case_result_path = Path(str(governance.get("case_result_path", "")))
    if case_result_path and not case_result_path.is_absolute():
        case_result_path = REPO_ROOT / case_result_path
    case_result = _read_json(case_result_path) if case_result_path.exists() else {}
    route = governance.get("route", {})
    return RunDetailResponse(
        run_key=run_key,
        case_id=str(governance.get("case_id", "")),
        case_name=governance_path.parent.name,
        closed=bool(case_result.get("closed")),
        failure_mode=case_result.get("failure_mode"),
        route=route,
        permissions=governance.get("permissions", {}),
        safety=governance.get("safety", {}),
        closure=governance.get("closure", {}),
        evidence=governance.get("public_evidence", []),
        claim_trace=governance.get("claim_trace", []),
        audit_events=governance.get("audit_events", []),
        artifact_paths={
            "governance": str(governance_path),
            "case_result": str(case_result_path),
            "audit": str(governance.get("audit_path", "")),
        },
        governance=governance,
        case_result=case_result,
        rag=governance.get("rag", {}),
    )


def get_run_audit(run_key: str, output_root: str | None = None) -> list[AuditEventResponse]:
    detail = get_run_detail(run_key, output_root)
    audit_path = Path(detail.artifact_paths.get("audit", ""))
    if not audit_path.exists():
        return []
    events: list[AuditEventResponse] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(
            AuditEventResponse(
                event_id=str(raw.get("event_id", "")),
                case_id=str(raw.get("case_id", "")),
                run_id=str(raw.get("run_id", "")),
                event_type=str(raw.get("event_type", "")),
                timestamp=float(raw.get("timestamp", 0.0) or 0.0),
                payload=dict(raw.get("payload", {})),
            )
        )
    return sorted(
        events,
        key=lambda event: (
            AUDIT_STAGE_ORDER.get(event.event_type, 1000),
            event.timestamp,
            event.event_id,
        ),
    )


def run_governance_case(request: RunGovernanceRequest) -> RunGovernanceResponse:
    output_root = _resolve_path(request.output_root, DEFAULT_OUTPUT_ROOT / "live-runs")
    output_root.mkdir(parents=True, exist_ok=True)
    prompt = request.service_request or request.prompt or ""
    eval_name = "service-governance-request"
    if not prompt.strip():
        raise ValueError("service_request is required")

    routing_prompt = _extract_case_prompt(prompt)
    run_name = f"service-{_text_digest(routing_prompt)}-{int(time())}"
    run_dir = output_root / run_name
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    case_id = f"service-{_text_digest(routing_prompt)}"
    run_id = f"web-governance-{_text_digest(run_name)}"
    audit_path = run_dir / "audit.jsonl"
    governance_path = run_dir / "governance.json"
    case_result_path = outputs_dir / "case_result.json"

    backend = RagGovernanceBackend(
        wiki_path=_resolve_required_path(request.wiki_path),
        embedding=request.embedding,
        kb_path=_resolve_path(
            request.kb_path,
            _default_kb_path(_resolve_required_path(request.wiki_path), request.embedding),
        ),
        top_k=request.top_k,
    )
    domain_resolver = (
        GovernanceDomainResolver(domain_pack_paths=request.governance_pack_paths)
        if request.governance_pack_paths
        else GovernanceDomainResolver()
    )
    pipeline_result = StageGovernancePipeline(
        intent_classifier=build_governance_intent_classifier(),
        domain_resolver=domain_resolver,
    ).run(
        prompt=prompt,
        routing_prompt=routing_prompt,
        case_id=case_id,
        run_id=run_id,
        backend=backend,
        audit_path=audit_path,
        approvals=request.approvals,
    )
    governance = {
        **pipeline_result.governance_payload,
        "case_result_path": str(case_result_path),
        "rag": backend.last_rag_log,
    }
    case_result = pipeline_result.governed_case_result
    governance_path.write_text(
        json.dumps(governance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    case_result_path.write_text(
        json.dumps(case_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return RunGovernanceResponse(
        run_key=_run_key(governance_path),
        case_id=case_id,
        case_name=eval_name,
        closed=bool(case_result.get("closed")),
        failure_mode=case_result.get("failure_mode"),
        route=governance.get("route", {}),
        permissions=governance.get("permissions", {}),
        safety=governance.get("safety", {}),
        closure=governance.get("closure", {}),
        evidence=governance.get("public_evidence", []),
        claim_trace=governance.get("claim_trace", []),
        audit_events=governance.get("audit_events", []),
        artifact_paths={
            "governance": str(governance_path),
            "case_result": str(case_result_path),
            "audit": str(audit_path),
        },
        governance=governance,
        case_result=case_result,
        rag=backend.last_rag_log,
    )


async def query_rag(request: RagQueryRequest) -> RagQueryResponse:
    wiki_path = _resolve_required_path(request.wiki_path)
    kb_path = _resolve_path(
        request.kb_path,
        _default_kb_path(wiki_path, request.embedding),
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


def record_feedback(request: FeedbackRequest) -> FeedbackResponse:
    governance_path = _resolve_required_path(request.governance_path)
    case_result_path = _resolve_required_path(request.case_result_path)
    governance = _read_json(governance_path)
    case_result = _read_json(case_result_path)
    case_id = str(
        governance.get("case_id")
        or case_result.get("case_id")
        or governance_path.parent.name
    )
    record = CaseFeedbackRecord.from_governance(
        case_id=case_id,
        prompt=str(governance.get("routing_prompt", "")),
        governance=governance,
        governed_case_result=case_result,
        labels=request.labels,
        note=request.note,
    )
    output_dir = _resolve_path(request.output_dir, DEFAULT_FEEDBACK_ROOT)
    paths = write_feedback_artifacts(record, output_dir=output_dir)
    return FeedbackResponse(
        case_id=case_id,
        labels=record.labels,
        artifact_paths=paths,
    )


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


def _extract_case_prompt(prompt: str) -> str:
    marker = "Case:"
    if marker not in prompt:
        return prompt.strip()
    case_text = prompt.split(marker, 1)[1].strip()
    for stop_marker in ["Use only", "Produce the required"]:
        if stop_marker in case_text:
            case_text = case_text.split(stop_marker, 1)[0].strip()
    return case_text or prompt.strip()


def _default_kb_path(wiki_path: Path, embedding: EmbeddingMode) -> Path:
    digest = hashlib.sha1(str(wiki_path).encode("utf-8")).hexdigest()[:12]
    return DEFAULT_OUTPUT_ROOT / "cache" / f"rag-{embedding}-{digest}.json"


def _find_run_governance_path(run_key: str, output_root: str | None) -> Path:
    root = _resolve_path(output_root, DEFAULT_OUTPUT_ROOT)
    for path in root.rglob("governance.json"):
        if _run_key(path) == run_key:
            return path
    raise FileNotFoundError(f"run not found: {run_key}")


def _run_key(governance_path: Path) -> str:
    try:
        relative = governance_path.relative_to(DEFAULT_OUTPUT_ROOT)
    except ValueError:
        relative = governance_path
    return hashlib.sha1(str(relative).encode("utf-8")).hexdigest()[:16]


def _text_digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def _resolve_required_path(value: str | Path) -> Path:
    path = _resolve_path(value, Path())
    if not path.exists():
        raise FileNotFoundError(f"path not found: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
