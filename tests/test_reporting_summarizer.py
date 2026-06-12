"""Tests for deterministic run summaries."""

from __future__ import annotations

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.reporting import summarize_agent_run, summarize_board
from openagents_orchestration.core.state_board import Budget, StateBoard


def test_agent_summary_records_failure_fields():
    transcript = [
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "read_file"}},
                {"function": {"name": "write_file"}},
            ],
        }
    ]

    summary = summarize_agent_run(
        agent_id="coder-t1",
        task_id="t1",
        status="failed",
        error="Server disconnected",
        transcript=transcript,
        artifacts=["app.py"],
        retry_count=2,
        steps_used=7,
        token_used=123,
    )

    assert summary["tools_called"] == ["read_file", "write_file"]
    assert summary["modified_or_created_files"] == ["app.py"]
    assert summary["last_successful_step"] == "last tool call: write_file"
    assert summary["failure_type"] == "transient_upstream"
    assert summary["retry_count"] == 2
    assert summary["partial_artifacts_reusable"] is True


def test_board_summary_includes_orchestration_recovery_context():
    board = StateBoard("obj", budget=Budget(token_limit=1000))
    board.add_tasks(TaskGraph(
        objective="obj",
        tasks=[
            TaskNode("t1", "build first piece", "coder", expected_artifacts=["a.py"]),
            TaskNode("t2", "continue after first", "coder", dependencies=["t1"]),
        ],
    ))
    board.register_agent("coder-t1", "coder")
    board.verify_artifact("a.py", exists=True)
    board.update_task("t1", status=TaskStatus.RUNNING)
    board.update_task("t1", status=TaskStatus.FAILED, error="Server disconnected")
    summary = summarize_agent_run(
        agent_id="coder-t1",
        task_id="t1",
        status="failed",
        error="Server disconnected",
        artifacts=["a.py"],
        retry_count=1,
    )
    board.log_event(
        "agent.run_summary",
        task_id="t1",
        agent_id="coder-t1",
        message="failed: transient_upstream",
        summary=summary,
    )

    summaries = summarize_board(board)
    orchestration = summaries["orchestration_summary"]

    assert "coder-t1" in summaries["agent_run_summaries"]
    assert orchestration["failed_tasks"] == ["t1"]
    assert orchestration["blocked_tasks"] == ["t2"]
    assert orchestration["failed_task_context"][0]["agent_summary"]["partial_artifacts_reusable"] is True
    assert "a.py" in orchestration["current_repo_state"]["verified_artifacts"]
    assert orchestration["recommended_minimal_next_task"]["task_id"] == "t1"
    assert orchestration["recovery_plan"]["mode"] == "recover_failed_task"
    assert orchestration["recovery_plan"]["task_id"] == "t1"
    assert orchestration["recovery_plan"]["failure_type"] == "transient_upstream"
    assert orchestration["recovery_plan"]["reusable_artifacts"] == ["a.py"]
    assert "retry" in orchestration["next_steps"].lower()


def test_board_summary_keeps_review_and_fix_tasks_unfinished():
    board = StateBoard("obj")
    board.add_tasks(TaskGraph(
        objective="obj",
        tasks=[
            TaskNode("t1", "review me", "coder", status=TaskStatus.REVIEW),
            TaskNode("t2", "fix me", "coder", status=TaskStatus.FIX_NEEDED),
        ],
    ))

    orchestration = summarize_board(board)["orchestration_summary"]

    assert orchestration["review_tasks"] == ["t1"]
    assert orchestration["fix_needed_tasks"] == ["t2"]
    assert orchestration["recommended_minimal_next_task"]["task_id"] == "t2"
    assert orchestration["recovery_plan"]["mode"] == "continue_fix_loop"
    assert "final verification" not in orchestration["next_steps"].lower()
