from pathlib import Path

from openagents_orchestration.backend.governed import GovernedBackendDispatcher
from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.router import GovernancePlan


def test_dispatcher_selects_human_backend_for_required_handoff(tmp_path: Path) -> None:
    backend = GovernedBackendDispatcher(
        wiki_path=None,
        embedding="mock",
        kb_path=None,
        top_k=1,
    )
    route_plan = GovernancePlan(
        route_label="service_desk",
        backends=["human_channel"],
        needs_human=True,
        confidence=0.8,
        business_process="identity_and_access_support",
    )

    output = backend.run(
        case_id="case-1",
        run_id="run-1",
        prompt="Need approval",
        route_plan=route_plan,
        audit_store=AuditStore(tmp_path / "audit.jsonl"),
    )

    assert backend.selected_backend == "human"
    assert backend.execution_mode == "human_governed"
    assert output["backend"] == "human"
    assert output["actions"] == ["create_handoff"]
    assert output["human_questions"]
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "backend_selected" in audit_text
    assert "human" in audit_text


def test_dispatcher_requires_wiki_only_for_rag_backend(tmp_path: Path) -> None:
    backend = GovernedBackendDispatcher(
        wiki_path=None,
        embedding="mock",
        kb_path=None,
        top_k=1,
    )
    route_plan = GovernancePlan(
        route_label="service_desk",
        backends=["rag_retrieval"],
        needs_human=False,
        confidence=0.8,
        business_process="knowledge",
    )

    try:
        backend.run(
            case_id="case-rag",
            run_id="run-rag",
            prompt="Need documented evidence",
            route_plan=route_plan,
            audit_store=AuditStore(tmp_path / "audit-rag.jsonl"),
        )
    except ValueError as exc:
        assert "wiki_path" in str(exc)
    else:
        raise AssertionError("RAG backend should require wiki_path")


def test_dispatcher_runs_rag_before_handoff_for_mixed_route(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "approval.md").write_text(
        "Access approvals require source evidence, owner approval, and scope.",
        encoding="utf-8",
    )
    backend = GovernedBackendDispatcher(
        wiki_path=wiki,
        embedding="mock",
        kb_path=tmp_path / "kb-mixed.json",
        top_k=1,
    )
    route_plan = GovernancePlan(
        route_label="service_desk",
        backends=["rag_retrieval", "human_channel"],
        needs_human=True,
        confidence=0.8,
        business_process="developer_platform_access_approval",
    )

    output = backend.run(
        case_id="case-mixed",
        run_id="run-mixed",
        prompt="Access approvals require owner approval and scope.",
        route_plan=route_plan,
        audit_store=AuditStore(tmp_path / "audit-mixed.jsonl"),
    )

    assert backend.selected_backend == "rag"
    assert output["backend"] == "rag"
    assert output["actions"] == ["create_handoff"]
    assert output["rag_log"]["retrieval"]["passages"]
    assert output["human_questions"]


def test_dispatcher_selects_subagent_backend_for_complex_service_case(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "incident.md").write_text(
        "sastoj database defect triage requires logs and reproduction evidence.",
        encoding="utf-8",
    )
    backend = GovernedBackendDispatcher(
        wiki_path=wiki,
        embedding="mock",
        kb_path=tmp_path / "kb.json",
        top_k=1,
    )
    route_plan = GovernancePlan(
        route_label="service_desk",
        backends=["rag_retrieval", "subagent"],
        needs_human=False,
        confidence=0.8,
        business_process="developer_service_defect_triage",
    )

    output = backend.run(
        case_id="case-2",
        run_id="run-2",
        prompt="sastoj database defect triage requires logs and reproduction",
        route_plan=route_plan,
        audit_store=AuditStore(tmp_path / "audit-subagent.jsonl"),
    )

    assert backend.selected_backend == "subagent"
    assert backend.execution_mode == "subagent_governed"
    assert output["backend"] == "subagent"
    assert output["rag_log"]
