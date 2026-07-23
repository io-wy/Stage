"""Facade for Stage local HTTP console services."""

from openagents_orchestration.service.cases import (
    health_payload,
    list_demo_cases,
    run_demo_case,
    run_governance_case,
)
from openagents_orchestration.service.feedback import record_feedback
from openagents_orchestration.service.rag import query_rag
from openagents_orchestration.service.runs import (
    get_run_audit,
    get_run_detail,
    list_run_history,
)

__all__ = [
    "get_run_audit",
    "get_run_detail",
    "health_payload",
    "list_demo_cases",
    "list_run_history",
    "query_rag",
    "record_feedback",
    "run_demo_case",
    "run_governance_case",
]
