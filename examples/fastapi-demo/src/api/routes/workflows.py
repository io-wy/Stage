from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from openagents_orchestration.app.api.deps import OrchestrationServiceDependency
from openagents_orchestration.app.schemas.workflow import WorkflowCreate, WorkflowRead, WorkflowUpdate
from openagents_orchestration.app.schemas.workflow_runtime import (
    HumanQuestionCreate,
    HumanQuestionRead,
    HumanQuestionReply,
    MessageCreate,
    MessageRead,
)
from openagents_orchestration.app.services.orchestration import OrchestrationService

router = APIRouter(tags=["workflows"])


@router.get("/workflows", response_model=list[WorkflowRead])
def list_workflows(service: OrchestrationService = Depends(OrchestrationServiceDependency)) -> list[dict[str, Any]]:
    return service.list_workflows()


@router.post("/workflows", response_model=WorkflowRead, status_code=201)
def create_workflow(
    payload: WorkflowCreate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.create_workflow(payload)


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
def get_workflow(
    workflow_id: str,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.get_workflow(workflow_id)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowRead)
def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.update_workflow(workflow_id, payload)


@router.get("/messages", response_model=list[MessageRead])
def list_messages(
    recipient: str | None = None,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> list[dict[str, Any]]:
    return service.list_messages(recipient)


@router.post("/messages", response_model=MessageRead, status_code=201)
def send_message(
    payload: MessageCreate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.send_message(payload.from_id, payload.to_id, payload.content)


@router.delete("/messages")
def clear_messages(
    recipient: str | None = None,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, int]:
    return service.clear_messages(recipient)


@router.get("/human-questions", response_model=list[HumanQuestionRead])
def list_human_questions(
    answered: bool | None = None,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> list[dict[str, Any]]:
    return service.list_human_questions(answered)


@router.post("/human-questions", response_model=HumanQuestionRead, status_code=201)
def create_human_question(
    payload: HumanQuestionCreate,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.create_human_question(payload.question, payload.options, payload.from_agent)


@router.post("/human-questions/{question_id}/reply", response_model=HumanQuestionRead)
def reply_human_question(
    question_id: str,
    payload: HumanQuestionReply,
    service: OrchestrationService = Depends(OrchestrationServiceDependency),
) -> dict[str, Any]:
    return service.reply_human_question(question_id, payload.answer)
