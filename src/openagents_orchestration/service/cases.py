"""Case execution services for the Stage console."""

from __future__ import annotations

import json
from pathlib import Path
from time import time

from openagents_orchestration.backend.governed import GovernedBackendDispatcher
from openagents_orchestration.control.domain import GovernanceDomainResolver
from openagents_orchestration.control.intent_llm import (
    build_governance_intent_classifier,
)
from openagents_orchestration.control.pipeline import StageGovernancePipeline
from openagents_orchestration.handler.http.schemas import (
    DemoCaseSummary,
    HealthResponse,
    RunDemoCaseRequest,
    RunDemoCaseResponse,
    RunGovernanceRequest,
    RunGovernanceResponse,
)
from openagents_orchestration.service.common import (
    extract_case_prompt,
    resolve_optional_wiki_path,
    resolve_path,
    run_key,
    text_digest,
)
from openagents_orchestration.service.settings import (
    DEFAULT_BASELINE_WORKSPACE,
    DEFAULT_EVALS_JSON,
    DEFAULT_FEEDBACK_ROOT,
    DEFAULT_OUTPUT_ROOT,
)


def health_payload() -> dict[str, str]:
    return HealthResponse(
        status="ok",
        default_output_root=str(DEFAULT_OUTPUT_ROOT),
        default_feedback_root=str(DEFAULT_FEEDBACK_ROOT),
    ).model_dump()


def list_demo_cases(evals_json: str | None = None) -> list[DemoCaseSummary]:
    from eval.case_handling.stage_governance_runner import load_hard_v2_eval_specs

    specs = load_hard_v2_eval_specs(resolve_path(evals_json, DEFAULT_EVALS_JSON))
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

    evals_json = resolve_path(request.evals_json, DEFAULT_EVALS_JSON)
    baseline_workspace = resolve_path(
        request.baseline_workspace,
        DEFAULT_BASELINE_WORKSPACE,
    )
    output_root = resolve_path(request.output_root, DEFAULT_OUTPUT_ROOT)
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


def run_governance_case(request: RunGovernanceRequest) -> RunGovernanceResponse:
    output_root = resolve_path(request.output_root, DEFAULT_OUTPUT_ROOT / "live-runs")
    output_root.mkdir(parents=True, exist_ok=True)
    prompt = request.service_request or request.prompt or ""
    eval_name = "service-governance-request"
    if not prompt.strip():
        raise ValueError("service_request is required")

    wiki_path = resolve_optional_wiki_path(request.wiki_path)
    routing_prompt = extract_case_prompt(prompt)
    run_name = f"service-{text_digest(routing_prompt)}-{int(time())}"
    run_dir = output_root / run_name
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    case_id = f"service-{text_digest(routing_prompt)}"
    run_id = f"web-governance-{text_digest(run_name)}"
    audit_path = run_dir / "audit.jsonl"
    governance_path = run_dir / "governance.json"
    case_result_path = outputs_dir / "case_result.json"

    backend = GovernedBackendDispatcher(
        wiki_path=wiki_path,
        embedding=request.embedding,
        kb_path=resolve_path(request.kb_path, Path()) if request.kb_path else None,
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
        "selected_backend": backend.selected_backend,
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
        run_key=run_key(governance_path),
        case_id=case_id,
        case_name=eval_name,
        selected_backend=backend.selected_backend,
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
