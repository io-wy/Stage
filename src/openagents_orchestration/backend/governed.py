"""Dispatch governed requests to the concrete backend selected by routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openagents_orchestration.backend.claude_code import (
    ClaudeCodeBackend,
    ClaudeCodeRunner,
)
from openagents_orchestration.backend.contracts import CaseBackend
from openagents_orchestration.backend.rag import RagGovernanceBackend, default_kb_path
from openagents_orchestration.control.audit import AuditStore
from openagents_orchestration.control.models import CaseAuditEvent
from openagents_orchestration.control.router import GovernancePlan
from openagents_orchestration.handler.http.schemas import EmbeddingMode


class HumanGovernanceBackend:
    """Backend that turns governed requests into explicit human handoff."""

    execution_mode = "human_governed"

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: GovernancePlan,
        audit_store: AuditStore,
    ) -> dict[str, Any]:
        questions = _route_human_questions(route_plan)
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="tool_invoked",
                payload={
                    "backend": "human",
                    "execution_mode": self.execution_mode,
                    "route": route_plan.to_dict(),
                    "questions": questions,
                },
            )
        )
        return {
            "backend": "human",
            "family": _family_for_route(route_plan),
            "closed": False,
            "answer": "该请求需要人工确认后才能继续闭环。",
            "actions": ["create_handoff"],
            "evidence": [],
            "human_questions": questions,
            "failure_mode": "missing_required_information",
            "confidence": route_plan.confidence,
        }


class SubagentGovernanceBackend:
    """Backend for multi-step service triage and synthesis.

    The first implementation uses the same external knowledge source as the RAG
    backend, but it owns a separate execution mode so routing can select a real
    subagent backend without pretending it was plain retrieval.
    """

    execution_mode = "subagent_governed"

    def __init__(self, rag_backend: RagGovernanceBackend):
        self._rag_backend = rag_backend

    @property
    def last_rag_log(self) -> dict[str, Any]:
        return self._rag_backend.last_rag_log

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
                    "backend": "subagent",
                    "execution_mode": self.execution_mode,
                    "route": route_plan.to_dict(),
                },
            )
        )
        output = self._rag_backend.run(
            case_id=case_id,
            run_id=run_id,
            prompt=prompt,
            route_plan=route_plan,
            audit_store=audit_store,
        )
        questions = list(output.get("human_questions", []))
        if route_plan.needs_human and not questions:
            questions = _route_human_questions(route_plan)
        return {
            **output,
            "backend": "subagent",
            "closed": bool(output.get("closed")) and not questions,
            "actions": ["answer_user"] if output.get("closed") and not questions else ["create_handoff"],
            "human_questions": questions,
            "metadata": {
                **dict(output.get("metadata", {})),
                "subagent_mode": "rag_assisted_triage",
            },
        }


class GovernedBackendDispatcher:
    """Single backend entry point that delegates according to a route plan."""

    execution_mode = "governed_dispatcher"

    def __init__(
        self,
        *,
        wiki_path: Path | None,
        embedding: EmbeddingMode,
        kb_path: Path | None,
        top_k: int,
        claude_code_runner: ClaudeCodeRunner | None = None,
    ):
        self._wiki_path = wiki_path
        self._embedding = embedding
        self._kb_path = kb_path
        self._top_k = top_k
        self._rag_backend: RagGovernanceBackend | None = None
        self._human_backend = HumanGovernanceBackend()
        self._subagent_backend: SubagentGovernanceBackend | None = None
        self._claude_code_backend = ClaudeCodeBackend(runner=claude_code_runner)
        self.selected_backend = ""

    @property
    def last_rag_log(self) -> dict[str, Any]:
        if self._rag_backend is None:
            return {}
        return self._rag_backend.last_rag_log

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: GovernancePlan,
        audit_store: AuditStore,
    ) -> dict[str, Any]:
        backend = self._select_backend(route_plan)
        self.selected_backend = _backend_name(backend)
        self.execution_mode = backend.execution_mode
        audit_store.append(
            CaseAuditEvent(
                case_id=case_id,
                run_id=run_id,
                event_type="backend_selected",
                payload={
                    "selected_backend": self.selected_backend,
                    "execution_mode": backend.execution_mode,
                    "route": route_plan.to_dict(),
                },
            )
        )
        output = backend.run(
            case_id=case_id,
            run_id=run_id,
            prompt=prompt,
            route_plan=route_plan,
            audit_store=audit_store,
        )
        return {
            **output,
            "selected_backend": self.selected_backend,
        }

    def _select_backend(self, route_plan: GovernancePlan) -> CaseBackend:
        backends = set(route_plan.backends)
        if "subagent" in backends:
            return self._subagent()
        if "claude_code" in backends:
            return self._claude_code_backend
        if "rag_retrieval" in backends:
            return self._rag()
        if "human_channel" in backends:
            return self._human_backend
        return self._rag()

    def _rag(self) -> RagGovernanceBackend:
        if self._wiki_path is None:
            raise ValueError("wiki_path is required or STAGE_WIKI_PATH must be set")
        if self._rag_backend is None:
            self._rag_backend = RagGovernanceBackend(
                wiki_path=self._wiki_path,
                embedding=self._embedding,
                kb_path=self._kb_path or default_kb_path(self._wiki_path, self._embedding),
                top_k=self._top_k,
            )
        return self._rag_backend

    def _subagent(self) -> SubagentGovernanceBackend:
        if self._subagent_backend is None:
            self._subagent_backend = SubagentGovernanceBackend(self._rag())
        return self._subagent_backend


def _backend_name(backend: CaseBackend) -> str:
    if isinstance(backend, HumanGovernanceBackend):
        return "human"
    if isinstance(backend, SubagentGovernanceBackend):
        return "subagent"
    if isinstance(backend, ClaudeCodeBackend):
        return "claude_code"
    if isinstance(backend, RagGovernanceBackend):
        return "rag"
    return "unknown"


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
