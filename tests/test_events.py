"""Tests for OrchestrationEvent."""

from __future__ import annotations

from openagents_orchestration.enterprise.events import OrchestrationEvent


class TestOrchestrationEvent:
    def test_event_values(self):
        assert OrchestrationEvent.PROJECT_CREATED == "project.created"
        assert OrchestrationEvent.TEAM_STARTED == "team.started"
        assert OrchestrationEvent.AGENT_SPAWNED == "agent.spawned"
        assert OrchestrationEvent.TASK_STATUS_CHANGED == "task.status_changed"
        assert OrchestrationEvent.HUMAN_QUESTION_ASKED == "human.question_asked"

    def test_category(self):
        assert OrchestrationEvent.PROJECT_STARTED.category == "project"
        assert OrchestrationEvent.TEAM_FAILED.category == "team"
        assert OrchestrationEvent.AGENT_ERROR.category == "agent"
        assert OrchestrationEvent.BUDGET_EXHAUSTED.category == "budget"

    def test_is_project_level(self):
        assert OrchestrationEvent.PROJECT_CREATED.is_project_level is True
        assert OrchestrationEvent.TASK_COMPLETED.is_project_level is True
        assert OrchestrationEvent.TRACE_STARTED.is_project_level is True

    def test_is_global_level(self):
        assert OrchestrationEvent.PROJECT_FAILED.is_global_level is True
        assert OrchestrationEvent.TEAM_FAILED.is_global_level is True
        assert OrchestrationEvent.AGENT_HEARTBEAT_TIMEOUT.is_global_level is True
        assert OrchestrationEvent.BUDGET_EXHAUSTED.is_global_level is True
        assert OrchestrationEvent.PROJECT_CREATED.is_global_level is False
        assert OrchestrationEvent.TASK_ADDED.is_global_level is False

    def test_from_value(self):
        e = OrchestrationEvent("project.started")
        assert e == OrchestrationEvent.PROJECT_STARTED

    def test_all_events_unique(self):
        values = [e.value for e in OrchestrationEvent]
        assert len(values) == len(set(values))
