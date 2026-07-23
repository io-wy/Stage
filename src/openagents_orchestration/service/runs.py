"""Run history, detail, and audit services for the Stage console."""

from __future__ import annotations

import json
from pathlib import Path

from openagents_orchestration.handler.http.schemas import (
    AuditEventResponse,
    RunDetailResponse,
    RunHistoryItem,
)
from openagents_orchestration.service.common import read_json, resolve_path, run_key
from openagents_orchestration.service.settings import DEFAULT_OUTPUT_ROOT, REPO_ROOT

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


def list_run_history(output_root: str | None = None) -> list[RunHistoryItem]:
    root = resolve_path(output_root, DEFAULT_OUTPUT_ROOT)
    if not root.exists():
        return []
    items: list[RunHistoryItem] = []
    for governance_path in sorted(
        root.rglob("governance.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        try:
            governance = read_json(governance_path)
        except json.JSONDecodeError:
            continue
        case_result_path = Path(str(governance.get("case_result_path", "")))
        if case_result_path and not case_result_path.is_absolute():
            case_result_path = REPO_ROOT / case_result_path
        case_result = read_json(case_result_path) if case_result_path.exists() else {}
        route = governance.get("route", {})
        domain = governance.get("domain", {})
        items.append(
            RunHistoryItem(
                run_key=run_key(governance_path),
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


def get_run_detail(run_key_value: str, output_root: str | None = None) -> RunDetailResponse:
    governance_path = _find_run_governance_path(run_key_value, output_root)
    governance = read_json(governance_path)
    case_result_path = Path(str(governance.get("case_result_path", "")))
    if case_result_path and not case_result_path.is_absolute():
        case_result_path = REPO_ROOT / case_result_path
    case_result = read_json(case_result_path) if case_result_path.exists() else {}
    route = governance.get("route", {})
    return RunDetailResponse(
        run_key=run_key_value,
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


def get_run_audit(
    run_key_value: str,
    output_root: str | None = None,
) -> list[AuditEventResponse]:
    detail = get_run_detail(run_key_value, output_root)
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


def _find_run_governance_path(run_key_value: str, output_root: str | None) -> Path:
    root = resolve_path(output_root, DEFAULT_OUTPUT_ROOT)
    for path in root.rglob("governance.json"):
        if run_key(path) == run_key_value:
            return path
    raise FileNotFoundError(f"run not found: {run_key_value}")

