from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from openagents_orchestration.app.api.deps import OrchestrationServiceDependency
from openagents_orchestration.app.core.config import get_settings
from openagents_orchestration.app.schemas.agent import ArtifactRead, BudgetRead, EventRead
from openagents_orchestration.app.schemas.run import ProgressRead, StateRead, TaskCreate, TaskRead, TaskUpdate
from openagents_orchestration.app.services.orchestration import OrchestrationService, read_memory_record

router = APIRouter(tags=["runs"])


@router.get("/state", response_model=StateRead)
def get_state(service: OrchestrationService = Depends(OrchestrationServiceDependency)) -> dict[str, Any]:
    return service.get_state()


@router.get("/runs/current", response_model=ProgressRead)
def get_current_run(service: OrchestrationService = Depends(OrchestrationServiceDependency)) -> dict[str, Any]:
    return service.get_progress()


@router.get("/tasks", response_model=list[TaskRead])
def list_tasks(
    status: str | None = None,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> list[dict[str, Any]]:
    return service.list_tasks(status)


@router.post("/tasks", response_model=TaskRead, status_code=201)
def create_task(
    payload: TaskCreate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.create_task(payload)


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(
    task_id: str,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.get_task(task_id)


@router.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.update_task(task_id, payload)


@router.get("/artifacts", response_model=list[ArtifactRead])
def list_artifacts(
    status: str | None = None,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> list[dict[str, Any]]:
    return service.list_artifacts(status)


@router.get("/events", response_model=list[EventRead])
def list_events(
    limit: int = Query(default=100, ge=1, le=1000),
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> list[dict[str, Any]]:
    return service.list_events(limit)


@router.get("/budget", response_model=BudgetRead)
def get_budget(service: OrchestrationService = Depends(OrchestrationServiceDependency)) -> dict[str, Any]:
    return service.get_budget()


@router.get("/memory/{session_id}")
def get_memory(session_id: str) -> dict[str, Any]:
    settings = get_settings()
    return read_memory_record(Path(settings.MEMORY_STORAGE_DIR), session_id) or {}


@router.get("/memory/{session_id}/search")
def search_memory(session_id: str, q: str = Query(min_length=1)) -> dict[str, Any]:
    settings = get_settings()
    record = read_memory_record(Path(settings.MEMORY_STORAGE_DIR), session_id) or {}
    results: list[dict[str, Any]] = []
    for item in record.get("summaries") or []:
        if isinstance(item, dict) and q.lower() in str(item.get("summary", "")).lower():
            results.append(item)
    return {"results": results}
