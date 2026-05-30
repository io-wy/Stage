from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)


class AgentUpdate(BaseModel):
    status: Literal["idle", "running", "stalled", "done", "failed"] | None = None
    current_task: str | None = None
    output_so_far: str | None = None
    files_claimed: list[str] | None = None
    files_verified: list[str] | None = None
    token_used: int | None = Field(default=None, ge=0)
    retry_count: int | None = Field(default=None, ge=0)
    steps_used: int | None = Field(default=None, ge=0)
    health_status: str | None = None


class AgentRead(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_id: str
    agent_type: str
    status: str
    current_task: str | None = None
    files_claimed: list[str] = []
    files_verified: list[str] = []
    elapsed_s: float = 0
    token_used: int = 0
    retry_count: int = 0
    steps_used: int = 0
    health_status: str = "healthy"


class ArtifactRead(BaseModel):
    path: str
    status: str
    claimed_by: str = ""


class BudgetRead(BaseModel):
    token_used: int
    token_limit: int
    token_remaining: int
    time_remaining_s: float
    steps_taken: int
    max_steps: int
    exhausted: bool


class EventRead(BaseModel):
    ts: float
    type: str
    task_id: str | None = None
    agent_id: str | None = None
    message: str = ""
    payload: dict[str, Any] = {}
