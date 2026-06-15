"""Tests for collaborative orchestration state transitions."""

from __future__ import annotations

import pytest

from openagents_orchestration.core.collaboration import (
    CollaborationSignal,
    parse_collaboration_message,
    task_id_from_resident_id,
)
from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.models.message import StructuredMessage
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.transport.channel_policy import ChannelPolicy


class _ResidentStub:
    def __init__(self, resident_id: str):
        self.resident_id = resident_id
        self.stopped = False
        self._sleeping = False

    async def stop(self) -> None:
        self.stopped = True
        self._sleeping = False

    async def sleep(self, reason: str = "") -> None:
        self._sleeping = True

    async def wake(self) -> None:
        self._sleeping = False


def _runner_with_task(status: TaskStatus = TaskStatus.RUNNING) -> tuple[OrchestratorRunner, StateBoard, TaskNode]:
    board = StateBoard("obj", echo=False, channel_policy=ChannelPolicy({"*": {"*"}}))
    task = TaskNode("api-auth", "implement auth", "coder", status=status)
    task.assigned_agent = "coder-api-auth"
    board.add_tasks(TaskGraph(objective="obj", tasks=[task]))

    runner = OrchestratorRunner("agent.json")
    runner._state_board = board
    runner._residents = {
        "coder-api-auth": _ResidentStub("coder-api-auth"),
        "reviewer-api-auth": _ResidentStub("reviewer-api-auth"),
    }
    return runner, board, task


def test_parse_collaboration_message_preserves_task_and_test_count():
    parsed = parse_collaboration_message(
        "TASK_REVIEW_READY[api-auth]: tests passed = 12",
        default_task_id="fallback",
    )

    assert parsed is not None
    assert parsed.signal == CollaborationSignal.REVIEW_READY
    assert parsed.task_id == "api-auth"
    assert parsed.tests_passed == 12
    assert task_id_from_resident_id("reviewer-api-auth") == "api-auth"


def test_collaborative_mode_can_be_forced_on_or_off():
    board = StateBoard("obj", echo=False)
    board.add_tasks(TaskGraph(objective="obj", tasks=[TaskNode("t1", "one coder", "coder")]))

    auto_runner = OrchestratorRunner("agent.json")
    auto_runner._state_board = board
    assert auto_runner._should_use_collaborative_mode() is False

    forced_runner = OrchestratorRunner("agent.json", collaborative_mode="on")
    forced_runner._state_board = board
    assert forced_runner._should_use_collaborative_mode() is True

    disabled_runner = OrchestratorRunner("agent.json", collaborative_mode="off")
    disabled_runner._state_board = board
    assert disabled_runner._should_use_collaborative_mode() is False


@pytest.mark.asyncio
async def test_process_collaborative_messages_approves_task_and_stops_residents():
    runner, board, task = _runner_with_task(TaskStatus.REVIEW)
    # Send APPROVED signal via Mailbox v2
    await board.send_structured(
        StructuredMessage.signal(
            "reviewer-api-auth", "reviewer-api-auth", "approved", "api-auth", text="LGTM"
        )
    )

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.COMPLETED
    assert task.assigned_agent == ""
    assert runner._residents == {}
    assert task.iteration_history[-1]["action"] == "reviewer_approved"


@pytest.mark.asyncio
async def test_process_collaborative_messages_requests_fix_once_and_preserves_coder():
    runner, board, task = _runner_with_task(TaskStatus.REVIEW)
    # Send FIX_NEEDED signal via Mailbox v2
    await board.send_structured(
        StructuredMessage.signal(
            "reviewer-api-auth",
            "reviewer-api-auth",
            "fix_needed",
            "api-auth",
            text="fix auth bug",
        )
    )

    await runner._process_collaborative_messages()
    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.FIX_NEEDED
    assert task.assigned_agent == "coder-api-auth"
    assert runner._residents["reviewer-api-auth"]._sleeping is True
    assert "coder-api-auth" in runner._residents
    assert len([h for h in task.iteration_history if h["action"] == "reviewer_requested_fix"]) == 1
    assert board.get_project_context()["recent_errors"][0]["error"].startswith("fix auth bug")


@pytest.mark.asyncio
async def test_review_ready_from_recipient_mailbox_uses_real_sender():
    """When coder sends REVIEW_READY to reviewer, the collaboration loop must
    process it even if the reviewer resident is not spawned yet, and must use
    the coder as the sender when validating state transitions.
    """
    runner, board, task = _runner_with_task(TaskStatus.RUNNING)
    # Ensure reviewer resident is NOT registered yet (realistic: coder finishes
    # before reviewer is spawned)
    runner._residents.pop("reviewer-api-auth", None)

    await board.send_structured(
        StructuredMessage.signal(
            "coder-api-auth", "reviewer-api-auth", "review_ready", "api-auth",
            text="TASK_REVIEW_READY[api-auth]: tests passed = 4",
            tests_passed=4,
        )
    )

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.REVIEW
    assert task.iteration_history[-1]["action"] == "coder_ready_for_review"


@pytest.mark.asyncio
async def test_process_collaborative_messages_ignores_wrong_task_marker():
    runner, board, task = _runner_with_task(TaskStatus.REVIEW)
    await board.send_structured(
        StructuredMessage.signal(
            "reviewer-api-auth", "reviewer-api-auth", "approved", "other-task", text="LGTM"
        )
    )

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.REVIEW


@pytest.mark.asyncio
async def test_process_collaborative_messages_preserves_non_signal_messages():
    """Non-signal messages addressed to a resident must remain in its mailbox."""
    runner, board, task = _runner_with_task(TaskStatus.RUNNING)
    await board.send_structured(
        StructuredMessage.from_text("director", "coder-api-auth", "normal chat")
    )

    await runner._process_collaborative_messages()

    # Task should not have changed state (no signal was processed)
    assert task.status == TaskStatus.RUNNING
    # The non-signal message should still be claimable by the agent itself
    remaining = await board.claim_messages("coder-api-auth", batch_size=10)
    assert len(remaining) == 1
    assert remaining[0].text == "normal chat"


def test_task_node_serializes_collaboration_fields():
    task = TaskNode(
        "research-api",
        "research API options",
        "researcher",
        collaboration_pattern="producer_checker",
        collaboration_participants={"producer": "researcher", "checker": "reviewer"},
    )

    restored = TaskNode.from_dict(task.to_dict())

    assert restored.collaboration_pattern == "producer_checker"
    assert restored.collaboration_participants == {"producer": "researcher", "checker": "reviewer"}


def test_task_node_loads_legacy_dict_without_collaboration_fields():
    task = TaskNode.from_dict({
        "task_id": "legacy",
        "description": "legacy task",
        "agent_type": "coder",
    })

    assert task.collaboration_pattern == "default"
    assert task.collaboration_participants == {}


def test_task_id_from_resident_id_accepts_any_agent_type():
    assert task_id_from_resident_id("researcher-api-auth") == "api-auth"
    assert task_id_from_resident_id("github_agent-create-pr") == "create-pr"
    assert task_id_from_resident_id("monitor-release-check") == "release-check"


@pytest.mark.asyncio
async def test_process_collaborative_messages_supports_researcher_reviewer_pattern():
    board = StateBoard("obj", echo=False, channel_policy=ChannelPolicy({"*": {"*"}}))
    task = TaskNode(
        "api-research",
        "research API options",
        "researcher",
        status=TaskStatus.RUNNING,
        collaboration_pattern="producer_checker",
        collaboration_participants={"producer": "researcher", "checker": "reviewer"},
    )
    task.assigned_agent = "researcher-api-research"
    board.add_tasks(TaskGraph(objective="obj", tasks=[task]))

    runner = OrchestratorRunner("agent.json")
    runner._state_board = board
    runner._residents = {
        "researcher-api-research": _ResidentStub("researcher-api-research"),
    }

    await board.send_structured(
        StructuredMessage.signal(
            "researcher-api-research",
            "reviewer-api-research",
            "review_ready",
            "api-research",
            text="TASK_REVIEW_READY[api-research]: tests passed = 1",
            tests_passed=1,
        )
    )

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.REVIEW
    assert runner._residents["researcher-api-research"]._sleeping is True


@pytest.mark.asyncio
async def test_process_collaborative_messages_approves_custom_checker_pattern():
    board = StateBoard("obj", echo=False, channel_policy=ChannelPolicy({"*": {"*"}}))
    task = TaskNode(
        "release-pr",
        "create and validate PR",
        "github_agent",
        status=TaskStatus.REVIEW,
        collaboration_pattern="producer_checker",
        collaboration_participants={"producer": "github_agent", "checker": "monitor"},
    )
    task.assigned_agent = "github_agent-release-pr"
    board.add_tasks(TaskGraph(objective="obj", tasks=[task]))

    runner = OrchestratorRunner("agent.json")
    runner._state_board = board
    runner._residents = {
        "github_agent-release-pr": _ResidentStub("github_agent-release-pr"),
        "monitor-release-pr": _ResidentStub("monitor-release-pr"),
    }

    await board.send_structured(
        StructuredMessage.signal(
            "monitor-release-pr",
            "github_agent-release-pr",
            "approved",
            "release-pr",
            text="TASK_APPROVED[release-pr]: LGTM",
        )
    )

    await runner._process_collaborative_messages()

    assert task.status == TaskStatus.COMPLETED
    assert task.assigned_agent == ""
    assert runner._residents == {}
