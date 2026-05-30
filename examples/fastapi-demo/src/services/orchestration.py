from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagents_orchestration.app.core.exceptions import ConflictError, NotFoundError
from openagents_orchestration.app.schemas.agent import AgentCreate, AgentUpdate
from openagents_orchestration.app.schemas.run import TaskCreate, TaskUpdate
from openagents_orchestration.app.schemas.workflow import WorkflowCreate, WorkflowUpdate
from openagents_orchestration.models.task import TaskNode, TaskStatus
from openagents_orchestration.state_board import StateBoard


@dataclass
class WorkflowRecord:
    workflow_id: str
    name: str
    description: str = ""
    task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "task_ids": self.task_ids,
        }


class OrchestrationService:
    """Small in-process service for API state.

    The service intentionally avoids external infrastructure. State is reset when
    the Python process restarts and is suitable for local development/tests.
    """

    def __init__(self, *, objective: str = "API-managed orchestration"):
        self.board = StateBoard(objective=objective, echo=False)
        self.workflows: dict[str, WorkflowRecord] = {}

    def reset(self) -> None:
        self.board = StateBoard(objective=self.board.objective, echo=False)
        self.workflows.clear()

    # State / tasks -----------------------------------------------------
    def get_state(self) -> dict[str, Any]:
        return self.board.to_dict()

    def get_progress(self) -> dict[str, Any]:
        return self.board.progress_summary()

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        task_status = TaskStatus(status) if status else None
        return [task.to_dict() for task in self.board.list_tasks(task_status)]

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self.board.get_task(task_id)
        if task is None:
            raise NotFoundError(f"Task '{task_id}' not found")
        return task.to_dict()

    def create_task(self, payload: TaskCreate) -> dict[str, Any]:
        if self.board.get_task(payload.task_id) is not None:
            raise ConflictError(f"Task '{payload.task_id}' already exists")
        missing = [dep for dep in payload.dependencies if self.board.get_task(dep) is None]
        if missing:
            raise NotFoundError(f"Unknown task dependencies: {', '.join(missing)}")
        task = TaskNode(
            task_id=payload.task_id,
            description=payload.description,
            agent_type=payload.agent_type,
            dependencies=payload.dependencies,
            expected_artifacts=payload.expected_artifacts,
            estimated_complexity=payload.estimated_complexity,
            input_context=payload.input_context,
        )
        self.board.add_task(task)
        return task.to_dict()

    def update_task(self, task_id: str, payload: TaskUpdate) -> dict[str, Any]:
        if self.board.get_task(task_id) is None:
            raise NotFoundError(f"Task '{task_id}' not found")
        fields = payload.model_dump(exclude_unset=True)
        if "dependencies" in fields:
            missing = [dep for dep in fields["dependencies"] if self.board.get_task(dep) is None]
            if missing:
                raise NotFoundError(f"Unknown task dependencies: {', '.join(missing)}")
        self.board.update_task(task_id, **fields)
        return self.get_task(task_id)

    # Agents ------------------------------------------------------------
    def list_agents(self, status: str | None = None) -> list[dict[str, Any]]:
        agents = [agent.to_dict() for agent in self.board.agents.values()]
        if status:
            agents = [agent for agent in agents if agent.get("status") == status]
        return agents

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.board.get_agent(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent '{agent_id}' not found")
        return agent.to_dict()

    def create_agent(self, payload: AgentCreate) -> dict[str, Any]:
        if self.board.get_agent(payload.agent_id) is not None:
            raise ConflictError(f"Agent '{payload.agent_id}' already exists")
        self.board.register_agent(payload.agent_id, payload.agent_type)
        return self.get_agent(payload.agent_id)

    def update_agent(self, agent_id: str, payload: AgentUpdate) -> dict[str, Any]:
        if self.board.get_agent(agent_id) is None:
            raise NotFoundError(f"Agent '{agent_id}' not found")
        self.board.update_agent(agent_id, **payload.model_dump(exclude_unset=True))
        return self.get_agent(agent_id)

    # Artifacts/events/budget -----------------------------------------
    def list_artifacts(self, status: str | None = None) -> list[dict[str, Any]]:
        artifacts = [artifact.to_dict() for artifact in self.board.artifacts.values()]
        if status:
            artifacts = [artifact for artifact in artifacts if artifact.get("status") == status]
        return artifacts

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        events = self.board.to_dict()["events"]
        return events[-limit:]

    def get_budget(self) -> dict[str, Any]:
        return self.board.budget.to_dict()

    # Mail / human questions ------------------------------------------
    def list_messages(self, recipient: str | None = None) -> list[dict[str, Any]]:
        if recipient:
            return self.board.messages_for(recipient)
        return list(self.board._pending_messages)

    def send_message(self, from_id: str, to_id: str, content: str) -> dict[str, Any]:
        self.board.send_mail(from_id, to_id, content)
        return self.board._pending_messages[-1]

    def clear_messages(self, recipient: str | None = None) -> dict[str, int]:
        return {"cleared": self.board.clear_mail(recipient)}

    def list_human_questions(self, answered: bool | None = None) -> list[dict[str, Any]]:
        return self.board.get_human_questions(answered)

    def create_human_question(self, question: str, options: str = "", from_agent: str = "") -> dict[str, Any]:
        qid = self.board.ask_human(question, options=options, from_agent=from_agent)
        return self.board.get_human_questions()[-1] | {"id": qid}

    def reply_human_question(self, question_id: str, answer: str) -> dict[str, Any]:
        if not self.board.reply_human(question_id, answer):
            raise NotFoundError(f"Unanswered human question '{question_id}' not found")
        for question in self.board.get_human_questions():
            if question["id"] == question_id:
                return question
        raise NotFoundError(f"Human question '{question_id}' not found")

    # Workflows --------------------------------------------------------
    def list_workflows(self) -> list[dict[str, Any]]:
        return [workflow.to_dict() for workflow in self.workflows.values()]

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.workflows.get(workflow_id)
        if workflow is None:
            raise NotFoundError(f"Workflow '{workflow_id}' not found")
        return workflow.to_dict()

    def create_workflow(self, payload: WorkflowCreate) -> dict[str, Any]:
        if payload.workflow_id in self.workflows:
            raise ConflictError(f"Workflow '{payload.workflow_id}' already exists")
        self._validate_task_ids(payload.task_ids)
        workflow = WorkflowRecord(
            workflow_id=payload.workflow_id,
            name=payload.name,
            description=payload.description,
            task_ids=payload.task_ids,
        )
        self.workflows[workflow.workflow_id] = workflow
        return workflow.to_dict()

    def update_workflow(self, workflow_id: str, payload: WorkflowUpdate) -> dict[str, Any]:
        workflow = self.workflows.get(workflow_id)
        if workflow is None:
            raise NotFoundError(f"Workflow '{workflow_id}' not found")
        fields = payload.model_dump(exclude_unset=True)
        if "task_ids" in fields:
            self._validate_task_ids(fields["task_ids"])
        for key, value in fields.items():
            setattr(workflow, key, value)
        return workflow.to_dict()

    def _validate_task_ids(self, task_ids: list[str]) -> None:
        missing = [task_id for task_id in task_ids if self.board.get_task(task_id) is None]
        if missing:
            raise NotFoundError(f"Unknown workflow task ids: {', '.join(missing)}")


_service = OrchestrationService()


def get_orchestration_service() -> OrchestrationService:
    return _service


def read_memory_record(storage_dir: Path, session_id: str) -> dict[str, Any] | None:
    if not session_id or any(ch in session_id for ch in "/\\."):
        raise NotFoundError("Invalid session id")
    path = storage_dir / f"{session_id}.json"
    if not path.exists():
        raise NotFoundError(f"Memory session '{session_id}' not found")
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
