"""Tests for StateBoard."""

from __future__ import annotations

from openagents_orchestration.core.state_board import AgentStatus, Budget, StateBoard
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class TestStateBoard:
    def test_init(self):
        board = StateBoard("test objective")
        assert board.objective == "test objective"
        assert board.tasks == {}
        assert board.agents == {}

    def test_add_tasks(self):
        board = StateBoard("obj")
        graph = TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder", dependencies=["t1"]),
            ],
        )
        board.add_tasks(graph)
        assert len(board.tasks) == 2
        assert board.get_task("t1").status == TaskStatus.PENDING

    def test_tasks_ready(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder", dependencies=["t1"]),
            ],
        ))
        ready = board.tasks_ready()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.COMPLETED)
        ready = board.tasks_ready()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"

    def test_tasks_blocked(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder", dependencies=["t1"]),
            ],
        ))
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.FAILED)
        blocked = board.tasks_blocked()
        assert len(blocked) == 1
        assert blocked[0].task_id == "t2"

    def test_agent_lifecycle(self):
        board = StateBoard("obj")
        board.register_agent("agent-1", "coder")
        assert board.get_agent("agent-1").status == AgentStatus.IDLE

        board.update_agent("agent-1", status=AgentStatus.RUNNING, current_task="t1")
        agent = board.get_agent("agent-1")
        assert agent.status == AgentStatus.RUNNING
        assert agent.current_task == "t1"

    def test_artifacts(self):
        board = StateBoard("obj")
        board.claim_artifact("t1", ["file.py", "test.py"])
        assert "file.py" in board.artifacts
        assert board.artifacts["file.py"].status == "claimed"

        board.verify_artifact("file.py", exists=True)
        assert board.artifacts["file.py"].status == "verified"


    def test_claim_artifact_does_not_downgrade_verified_record(self):
        board = StateBoard("obj")
        board.verify_artifact("file.py", exists=True)
        board.claim_artifact("t1", ["file.py"])

        assert board.artifacts["file.py"].status == "verified"
        assert board.artifacts["file.py"].claimed_by == "t1"

    def test_review_and_fix_needed_are_actionable(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))

        board.update_task("t1", status=TaskStatus.REVIEW)
        assert board.has_actionable()

        board.update_task("t1", status=TaskStatus.FIX_NEEDED)
        assert board.has_actionable()
        assert not board.all_terminal()

    def test_roundtrip_preserves_budget_context_and_residents(self):
        board = StateBoard("obj", budget=Budget(token_limit=1000, time_limit_s=1800, max_steps=99))
        board.budget.start_time = 123.0
        board.add_error_log("t1", "needs fix")

        from openagents_orchestration.core.resident import ResidentState
        board.register_resident(ResidentState(resident_id="coder-api-auth", agent_type="coder", latest_output="done"))

        restored = StateBoard.from_dict(board.to_dict(), reset_budget_clock=False)

        assert restored.budget.time_limit_s == 1800
        assert restored.budget.start_time == 123.0
        assert restored.get_project_context()["recent_errors"][0]["error"] == "needs fix"
        assert restored.get_resident("coder-api-auth").latest_output == "done"

    def test_budget(self):
        budget = Budget(token_limit=1000, max_steps=5)
        assert budget.token_remaining == 1000
        assert not budget.exhausted

        budget.token_used = 1000
        budget.steps_taken = 3
        assert budget.exhausted

    def test_snapshot(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.register_agent("a1", "coder")
        board.log_event("test", message="hello")

        snapshot = board.snapshot()
        assert snapshot["objective"] == "obj"
        assert "tasks" in snapshot
        assert "agents" in snapshot
        assert "signals" in snapshot
        assert "progress" in snapshot
        assert snapshot["signals"]["ready_to_run"] == ["t1"]
        assert snapshot["progress"]["ready_tasks"] == 1

    def test_all_terminal(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "task 1", "coder"),
                TaskNode("t2", "task 2", "coder"),
            ],
        ))
        assert not board.all_terminal()
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.COMPLETED)
        board.update_task("t2", status=TaskStatus.RUNNING)
        board.update_task("t2", status=TaskStatus.FAILED)
        assert board.all_terminal()

    def test_to_report(self):
        board = StateBoard("obj", budget=Budget(token_limit=50000))
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.update_task("t1", status=TaskStatus.RUNNING)
        board.update_task("t1", status=TaskStatus.COMPLETED, result_output="done")
        report = board.to_report()
        assert report.objective == "obj"
        assert report.success_rate == 1.0
        assert "completed" in report.summary

    def test_suggest_fallback_task_not_found(self):
        board = StateBoard("obj")
        assert board.suggest_fallback("nonexistent") == ""

    def test_suggest_fallback_high_fallback_attempts(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.register_agent("coder-t1", "coder")
        board.update_agent("coder-t1", fallback_attempts=2)
        suggestion = board.suggest_fallback("t1")
        assert "已连续失败 2 次" in suggestion
        assert "ask_human" in suggestion

    def test_suggest_fallback_step_exhausted_with_artifacts(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder", expected_artifacts=["a.py"])],
        ))
        board.register_agent("coder-t1", "coder")
        board.update_agent("coder-t1", steps_used=30)
        suggestion = board.suggest_fallback("t1")
        assert "30 步中产出了 1 个 artifact" in suggestion
        assert "replan 拆分" in suggestion

    def test_suggest_fallback_step_exhausted_no_artifacts(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.register_agent("coder-t1", "coder")
        board.update_agent("coder-t1", steps_used=30)
        suggestion = board.suggest_fallback("t1")
        assert "几乎没有产出" in suggestion
        assert "spawn resident" in suggestion

    def test_suggest_fallback_high_token(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.register_agent("coder-t1", "coder")
        board.update_agent("coder-t1", token_used=50000)
        suggestion = board.suggest_fallback("t1")
        assert "Token 消耗较高（50000）" in suggestion

    def test_suggest_fallback_low_budget(self):
        board = StateBoard("obj", budget=Budget(time_limit_s=60))
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        # Budget started 60s ago, so time_remaining ~ 0s
        import time
        board.budget.start_time = time.time() - 65
        suggestion = board.suggest_fallback("t1")
        assert "预算紧张" in suggestion
        assert "finalize" in suggestion

    def test_suggest_fallback_recommendation_in_error(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.update_task("t1", error="something failed  [recommendation: replan — bad path]")
        suggestion = board.suggest_fallback("t1")
        assert "spawn_agent 建议 replan" in suggestion

    def test_suggest_fallback_no_signals(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task 1", "coder")],
        ))
        board.register_agent("coder-t1", "coder")
        suggestion = board.suggest_fallback("t1")
        assert suggestion == ""
