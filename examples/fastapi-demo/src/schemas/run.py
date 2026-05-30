from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatusLiteral = Literal["pending", "running", "completed", "failed", "skipped"]


class TaskCreate(BaseModel):
    task_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)
    dependencies: list[str] = []
    expected_artifacts: list[str] = []
    estimated_complexity: int = Field(default=1, ge=1, le=5)
    input_context: str = ""


class TaskUpdate(BaseModel):
    description: str | None = None
    agent_type: str | None = None
    dependencies: list[str] | None = None
    expected_artifacts: list[str] | None = None
    estimated_complexity: int | None = Field(default=None, ge=1, le=5)
    status: TaskStatusLiteral | None = None
    error: str | None = None
    retry_count: int | None = Field(default=None, ge=0)
    input_context: str | None = None
    result_output: str | None = None
    actual_artifacts: list[str] | None = None


class TaskRead(BaseModel):
    task_id: str
    description: str
    agent_type: str
    dependencies: list[str]
    expected_artifacts: list[str]
    estimated_complexity: int
    status: str
    error: str | None = None
    retry_count: int
    input_context: str
    result_output: str
    actual_artifacts: list[str]


class StateRead(BaseModel):
    objective: str
    budget: dict[str, Any]
    tasks: list[dict[str, Any]]
    agents: dict[str, Any]
    residents: dict[str, Any]
    artifacts: dict[str, Any]
    events: list[dict[str, Any]]
    human_questions: list[dict[str, Any]]
    pending_messages: list[dict[str, Any]]
    final_summary: str


class ProgressRead(BaseModel):
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    skipped_tasks: int
    running_tasks: int
    ready_tasks: int
    blocked_tasks: int
    terminal_tasks: int
    all_done: bool
    waiting_for_human: int
