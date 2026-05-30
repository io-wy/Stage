from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from openagents_orchestration.app.api.deps import OrchestrationServiceDependency
from openagents_orchestration.app.schemas.agent import AgentCreate, AgentRead, AgentUpdate
from openagents_orchestration.app.services.orchestration import OrchestrationService

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentRead])
def list_agents(
    status: str | None = None,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> list[dict[str, Any]]:
    return service.list_agents(status)


@router.post("", response_model=AgentRead, status_code=201)
def create_agent(
    payload: AgentCreate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.create_agent(payload)


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(
    agent_id: str,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.get_agent(agent_id)


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.update_agent(agent_id, payload)
