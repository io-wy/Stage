from pathlib import Path

from openagents_orchestration.backend.governed import GovernedBackendDispatcher
from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.models import ActionPlan
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


def test_dispatcher_selects_claude_code_backend_without_wiki(tmp_path: Path) -> None:
    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {
            "exit_code": 0,
            "output": "created summarize_process_memory helper",
            "command": "claude -p <prompt>",
        }

    backend = GovernedBackendDispatcher(
        wiki_path=None,
        embedding="mock",
        kb_path=None,
        top_k=1,
        claude_code_runner=fake_runner,
    )
    route_plan = GovernancePlan(
        route_label="development",
        backends=["claude_code"],
        needs_human=False,
        confidence=0.9,
        business_process="code_task",
        action_plan=ActionPlan(
            case_id="case-code",
            run_id="run-code",
            executor="claude_code",
            side_effect_level="workspace_write",
        ),
    )

    output = backend.run(
        case_id="case-code",
        run_id="run-code",
        prompt="Write a summarize_process_memory helper",
        route_plan=route_plan,
        audit_store=AuditStore(tmp_path / "audit-code.jsonl"),
    )

    assert backend.selected_backend == "claude_code"
    assert backend.execution_mode == "claude_code_governed"
    assert output["backend"] == "claude_code"
    assert output["closed"] is True
    assert output["actions"] == ["propose_patch", "run_verification"]
    assert output["action_result"]["executed"] is True
    assert output["evidence"][0]["source_ref"] == "claude_code_output"
    assert calls[0]["instruction"].startswith("Stage governed code task")
    audit_text = (tmp_path / "audit-code.jsonl").read_text(encoding="utf-8")
    assert "backend_selected" in audit_text
    assert "claude_code" in audit_text


def test_dispatcher_classifies_claude_code_timeout(tmp_path: Path) -> None:
    def fake_runner(**kwargs):
        return {
            "exit_code": 124,
            "output": "claude-code timed out after 1s.\nstdout: \nstderr:",
            "command": "claude --safe-mode -p <prompt>",
        }

    backend = GovernedBackendDispatcher(
        wiki_path=None,
        embedding="mock",
        kb_path=None,
        top_k=1,
        claude_code_runner=fake_runner,
    )
    route_plan = GovernancePlan(
        route_label="development",
        backends=["claude_code"],
        needs_human=False,
        confidence=0.9,
        business_process="code_task",
        action_plan=ActionPlan(
            case_id="case-code-timeout",
            run_id="run-code-timeout",
            executor="claude_code",
            side_effect_level="workspace_write",
        ),
    )

    output = backend.run(
        case_id="case-code-timeout",
        run_id="run-code-timeout",
        prompt="Write a helper",
        route_plan=route_plan,
        audit_store=AuditStore(tmp_path / "audit-code-timeout.jsonl"),
    )

    assert output["closed"] is False
    assert output["failure_mode"] == "execution_timeout"
    assert output["actions"] == ["create_handoff"]
    assert output["action_result"]["executed"] is False
    assert "超时" in output["human_questions"][0]


def test_dispatcher_classifies_claude_code_budget_exhaustion(tmp_path: Path) -> None:
    def fake_runner(**kwargs):
        return {
            "exit_code": 1,
            "output": '{"subtype":"error_max_budget_usd","errors":["Reached maximum budget"]}',
            "command": "claude --safe-mode -p <prompt>",
        }

    backend = GovernedBackendDispatcher(
        wiki_path=None,
        embedding="mock",
        kb_path=None,
        top_k=1,
        claude_code_runner=fake_runner,
    )
    route_plan = GovernancePlan(
        route_label="development",
        backends=["claude_code"],
        needs_human=False,
        confidence=0.9,
        business_process="code_task",
        action_plan=ActionPlan(
            case_id="case-code-budget",
            run_id="run-code-budget",
            executor="claude_code",
            side_effect_level="workspace_write",
        ),
    )

    output = backend.run(
        case_id="case-code-budget",
        run_id="run-code-budget",
        prompt="Write a helper",
        route_plan=route_plan,
        audit_store=AuditStore(tmp_path / "audit-code-budget.jsonl"),
    )

    assert output["closed"] is False
    assert output["failure_mode"] == "over_budget"
    assert "预算" in output["human_questions"][0]


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
