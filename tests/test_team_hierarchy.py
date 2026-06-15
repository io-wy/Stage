"""Tests for Team hierarchy and subgraph support."""

from __future__ import annotations

from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import Budget, StateBoard
from openagents_orchestration.core.sub_state_board import SubStateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class TestTaskNodeSubgraph:
    def test_task_node_with_subgraph_roundtrip(self):
        inner = TaskGraph(
            objective="inner",
            tasks=[
                TaskNode("i1", "impl auth", "coder"),
                TaskNode("i2", "review auth", "reviewer", dependencies=["i1"]),
            ],
        )
        node = TaskNode("t1", "auth module", "team_leader", subgraph=inner)
        data = node.to_dict()
        assert "subgraph" in data
        assert data["subgraph"]["objective"] == "inner"
        restored = TaskNode.from_dict(data)
        assert restored.subgraph is not None
        assert len(restored.subgraph.tasks) == 2

    def test_task_node_without_subgraph_no_key(self):
        node = TaskNode("t1", "simple", "coder")
        data = node.to_dict()
        assert "subgraph" not in data


class TestSubStateBoard:
    def test_sub_board_budget_inherits_from_parent(self):
        parent = StateBoard("obj", budget=Budget(token_limit=10000, time_limit_s=400, max_steps=100))
        sub = SubStateBoard(parent=parent, objective="sub")
        assert sub.budget.token_limit <= 2500
        assert sub.budget.time_limit_s <= 100
        assert sub.budget.max_steps == 30

    def test_sub_board_logs_bubble_to_parent(self):
        parent = StateBoard("obj", echo=False)
        sub = SubStateBoard(parent=parent, objective="sub")
        sub.log_event("test.event", message="hello")
        assert any(e.event_type == "test.event" for e in sub.events)
        assert any(e.event_type == "sub.test.event" for e in parent.events)

    def test_sub_board_add_tokens_bubbles_event_not_parent_budget(self):
        parent = StateBoard("obj", budget=Budget(token_limit=1000, max_steps=20))
        sub = SubStateBoard(parent=parent, objective="sub")
        sub.add_tokens(50)
        assert sub.budget.token_used == 50
        # Parent budget is NOT directly modified; merged after team completion
        assert parent.budget.token_used == 0
        # But event is bubbled for observability
        assert any(e.event_type == "sub.budget.tokens" for e in parent.events)

    def test_sub_board_add_steps_bubbles_event_not_parent_budget(self):
        parent = StateBoard("obj", budget=Budget(token_limit=1000, max_steps=20))
        sub = SubStateBoard(parent=parent, objective="sub")
        sub.add_steps(3)
        assert sub.budget.steps_taken == 3
        assert parent.budget.steps_taken == 0
        assert any(e.event_type == "sub.budget.steps" for e in parent.events)

    def test_sub_board_task_isolation(self):
        parent = StateBoard("obj", echo=False)
        parent.add_tasks(TaskGraph("obj", [TaskNode("p1", "parent task", "coder")]))
        sub = SubStateBoard(parent=parent, objective="sub")
        sub.add_tasks(TaskGraph("sub", [TaskNode("s1", "sub task", "coder")]))
        assert len(parent.tasks) == 1
        assert len(sub.tasks) == 1
        assert "s1" not in parent.tasks


class TestTeamLeaderMerge:
    def test_summarize_team_sub_board_collects_outputs_and_artifacts(self):
        parent = StateBoard("obj", echo=False)
        sub = SubStateBoard(parent=parent, objective="team task")
        task = TaskNode("s1", "build module", "coder", status=TaskStatus.COMPLETED)
        task.result_output = "implemented registry and tests"
        task.actual_artifacts = ["pipeline_eval/registry.py"]
        sub.add_tasks(TaskGraph("team task", [task]))
        sub.claim_artifact("s1", ["pipeline_eval/cli.py"])
        sub.verify_artifact("pipeline_eval/cli.py", exists=True)

        runner = OrchestratorRunner("agent.json")
        summary, artifacts = runner._summarize_team_sub_board(sub)

        assert "Team completed 1/1 subtask" in summary
        assert "implemented registry and tests" in summary
        assert artifacts == ["pipeline_eval/registry.py", "pipeline_eval/cli.py"]
