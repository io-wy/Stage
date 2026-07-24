"""Pydantic schemas for the Stage local HTTP console."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EmbeddingMode = Literal["ollama", "mock"]


class HealthResponse(BaseModel):
    status: str
    default_output_root: str
    default_feedback_root: str


class DemoCaseSummary(BaseModel):
    eval_id: int
    name: str
    family: str
    business_process: str
    expected_closed: bool
    prompt: str


class RunDemoCaseRequest(BaseModel):
    eval_id: int
    evals_json: str | None = None
    baseline_workspace: str | None = None
    output_root: str | None = None


class RunDemoCaseResponse(BaseModel):
    eval_id: int
    eval_name: str
    closed: bool
    failure_mode: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    closure: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    claim_trace: list[dict[str, Any]] = Field(default_factory=list)
    audit_events: list[str] = Field(default_factory=list)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    governance: dict[str, Any] = Field(default_factory=dict)
    case_result: dict[str, Any] = Field(default_factory=dict)


class RunGovernanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_request: str | None = None
    prompt: str | None = None
    wiki_path: str | None = None
    governance_pack_paths: list[str] = Field(default_factory=list)
    approvals: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    embedding: EmbeddingMode = "ollama"
    top_k: int = 3
    kb_path: str | None = None
    output_root: str | None = None


class RunGovernanceResponse(BaseModel):
    run_key: str = ""
    case_id: str = ""
    case_name: str = ""
    selected_backend: str = ""
    closed: bool
    failure_mode: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    closure: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    claim_trace: list[dict[str, Any]] = Field(default_factory=list)
    audit_events: list[str] = Field(default_factory=list)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    governance: dict[str, Any] = Field(default_factory=dict)
    case_result: dict[str, Any] = Field(default_factory=dict)
    rag: dict[str, Any] = Field(default_factory=dict)


class RunHistoryItem(BaseModel):
    run_key: str
    case_id: str
    run_id: str
    eval_name: str = ""
    closed: bool | None = None
    failure_mode: str | None = None
    business_process: str = ""
    route_backends: list[str] = Field(default_factory=list)
    governance_path: str
    case_result_path: str = ""
    audit_path: str = ""
    created_at: float = 0.0


class RunDetailResponse(RunGovernanceResponse):
    pass


class AuditEventResponse(BaseModel):
    event_id: str = ""
    case_id: str = ""
    run_id: str = ""
    event_type: str
    timestamp: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)


class RagQueryRequest(BaseModel):
    wiki_path: str | None = None
    question: str
    top_k: int = 3
    embedding: EmbeddingMode = "ollama"
    kb_path: str | None = None


class RagPassageResponse(BaseModel):
    source: str
    score: float
    text: str
    tags: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class RagQueryResponse(BaseModel):
    question: str
    wiki_path: str
    embedding: EmbeddingMode
    kb_path: str
    chunk_count: int
    passages: list[RagPassageResponse]


class FeedbackRequest(BaseModel):
    governance_path: str
    case_result_path: str
    labels: list[str] = Field(default_factory=list)
    note: str = ""
    output_dir: str | None = None


class FeedbackResponse(BaseModel):
    case_id: str
    labels: list[str]
    artifact_paths: dict[str, str]
