"""Governance record models for Stage."""

from __future__ import annotations

from time import time
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class CaseRecord(BaseModel):
    case_id: str
    business_domain: str
    business_process: str
    requester: str = ""
    request_text: str = ""
    intent_frame: dict[str, Any] = Field(default_factory=dict)
    status: str = "intake"
    expected_closure_policy: str = "verify_before_close"
    created_at: float = Field(default_factory=time)
    updated_at: float = Field(default_factory=time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseRunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    backend_plan: list[str] = Field(default_factory=list)
    started_at: float = Field(default_factory=time)
    completed_at: float | None = None
    outcome: str = "running"
    budgets: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceEntry(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    run_id: str
    source_ref: str
    retrieval_query: str
    summary: str
    sensitivity: str = "unknown"
    used_by: list[str] = Field(default_factory=list)
    selected: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationRecord(BaseModel):
    invocation_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    run_id: str
    backend: str
    input_ref: str = ""
    output_ref: str = ""
    duration_seconds: float | None = None
    token_cost: int | None = None
    status: str = "ok"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SafetyFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    run_id: str
    surface: str
    severity: str
    finding_type: str
    blocked: bool = False
    path: list[str] = Field(default_factory=list)
    summary: str = ""


class VerificationFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    run_id: str
    gate: str
    passed: bool
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class HumanHandoffRecord(BaseModel):
    handoff_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    run_id: str
    question: str
    owner: str = ""
    expected_answer_schema: dict[str, Any] = Field(default_factory=dict)
    status: str = "open"
    created_at: float = Field(default_factory=time)


class CaseAuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    run_id: str
    event_type: str
    timestamp: float = Field(default_factory=time)
    payload: dict[str, Any] = Field(default_factory=dict)
