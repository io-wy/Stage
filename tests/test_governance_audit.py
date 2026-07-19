"""Tests for Stage governance audit storage."""

from __future__ import annotations

from openagents_orchestration.governance.audit import AuditStore, replay_case_state
from openagents_orchestration.governance.models import CaseAuditEvent


def test_audit_store_appends_and_reads_case_events(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)

    e1 = CaseAuditEvent(
        case_id="case-1",
        run_id="run-1",
        event_type="intent_classified",
        payload={"workflow_type": "service_case"},
    )
    e2 = CaseAuditEvent(
        case_id="case-1",
        run_id="run-1",
        event_type="backend_planned",
        payload={"backend_plan": ["rag_retrieval", "claude_code"]},
    )
    e3 = CaseAuditEvent(
        case_id="case-2",
        run_id="run-2",
        event_type="case_closed",
        payload={"closed": True},
    )

    store.append(e1)
    store.append(e2)
    store.append(e3)

    case_1_events = store.read_case("case-1")
    assert [event.event_type for event in case_1_events] == [
        "intent_classified",
        "backend_planned",
    ]
    assert case_1_events[0].payload["workflow_type"] == "service_case"
    assert case_1_events[1].payload["backend_plan"] == ["rag_retrieval", "claude_code"]


def test_replay_case_state_reconstructs_latest_state(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)

    store.append(
        CaseAuditEvent(
            case_id="case-1",
            run_id="run-1",
            event_type="intent_classified",
            payload={"workflow_type": "service_case", "business_process": "nas"},
        )
    )
    store.append(
        CaseAuditEvent(
            case_id="case-1",
            run_id="run-1",
            event_type="evidence_added",
            payload={"evidence_count": 2},
        )
    )
    store.append(
        CaseAuditEvent(
            case_id="case-1",
            run_id="run-1",
            event_type="case_closed",
            payload={"closed": True, "outcome": "done"},
        )
    )

    state = replay_case_state(store.read_case("case-1"))

    assert state["case_id"] == "case-1"
    assert state["run_id"] == "run-1"
    assert state["workflow_type"] == "service_case"
    assert state["business_process"] == "nas"
    assert state["evidence_count"] == 2
    assert state["closed"] is True
    assert state["outcome"] == "done"
