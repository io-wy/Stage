from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowCreate(BaseModel):
    workflow_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    task_ids: list[str] = []


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    task_ids: list[str] | None = None


class WorkflowRead(BaseModel):
    workflow_id: str
    name: str
    description: str
    task_ids: list[str]
