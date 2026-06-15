"""Tests for OrchestrationMetrics."""

from __future__ import annotations

from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.enterprise.metrics import OrchestrationMetrics


class TestOrchestrationMetrics:
    def test_gauge_set_and_get(self):
        m = OrchestrationMetrics()
        m.projects_total.set(3, labels={"status": "running"})
        assert m.projects_total.get(labels={"status": "running"}) == 3.0

    def test_counter_increment(self):
        m = OrchestrationMetrics()
        m.messages_delivered.inc(labels={"project_id": "p1", "topology": "p2p"})
        m.messages_delivered.inc(labels={"project_id": "p1", "topology": "p2p"})
        assert m.messages_delivered.get(labels={"project_id": "p1", "topology": "p2p"}) == 2.0

    def test_histogram_observe(self):
        m = OrchestrationMetrics()
        m.heartbeat_latency.observe(15.0, labels={"project_id": "p1", "agent_id": "a1"})
        m.heartbeat_latency.observe(45.0, labels={"project_id": "p1", "agent_id": "a1"})

        samples = m.heartbeat_latency.collect()
        # Should have buckets + Inf + sum + count
        assert len(samples) > 0
        count_samples = [s for s in samples if "_count" in s.name]
        assert len(count_samples) == 1
        assert count_samples[0].value == 2.0

    def test_collect_all(self):
        m = OrchestrationMetrics()
        m.projects_total.set(1, labels={"status": "running"})
        m.tasks_total.set(5, labels={"project_id": "p1", "status": "pending"})

        all_samples = m.collect()
        names = {s.name for s in all_samples}
        assert "orchestration_projects_total" in names
        assert "orchestration_tasks_total" in names

    def test_prometheus_format(self):
        m = OrchestrationMetrics()
        m.projects_total.set(2, labels={"status": "running"})
        text = m.to_prometheus()
        assert "# HELP orchestration_projects_total" in text
        assert "# TYPE orchestration_projects_total gauge" in text
        assert 'status="running"' in text
        assert "2" in text

    def test_from_state_board(self):
        board = StateBoard("test", echo=False)
        board.add_task = None  # will use direct task manipulation

        # Manually add tasks with different statuses
        from openagents_orchestration.models.task import TaskNode, TaskStatus

        board.tasks["t1"] = TaskNode("t1", "fix bug", "coder", status=TaskStatus.PENDING)
        board.tasks["t2"] = TaskNode("t2", "write tests", "coder", status=TaskStatus.RUNNING)

        m = OrchestrationMetrics()
        m.from_state(board, project_id="proj-1")

        assert m.tasks_total.get(labels={"project_id": "proj-1", "status": "pending"}) == 1.0
        assert m.tasks_total.get(labels={"project_id": "proj-1", "status": "running"}) == 1.0
        assert m.budget_tokens_used.get(labels={"project_id": "proj-1"}) == 0.0

    def test_gauge_inc_dec(self):
        m = OrchestrationMetrics()
        m.projects_total.inc(labels={"status": "running"}, amount=5)
        assert m.projects_total.get(labels={"status": "running"}) == 5.0
        m.projects_total.dec(labels={"status": "running"}, amount=2)
        assert m.projects_total.get(labels={"status": "running"}) == 3.0

    def test_different_label_sets(self):
        m = OrchestrationMetrics()
        m.projects_total.set(1, labels={"status": "running"})
        m.projects_total.set(2, labels={"status": "pending"})
        assert m.projects_total.get(labels={"status": "running"}) == 1.0
        assert m.projects_total.get(labels={"status": "pending"}) == 2.0
