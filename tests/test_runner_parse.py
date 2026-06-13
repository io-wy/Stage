"""Tests for OrchestratorRunner parsing logic."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
from openagents_orchestration.core.runner import OrchestratorRunner
from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.tools.director.spawn_agent import SpawnAgentTool


class TestTaskGraphParsing:
    """Test _parse_task_graph handles various LLM output formats."""

    def test_parse_json_with_fences(self):
        text = '```json\n{"tasks": [{"task_id": "t1", "description": "d", "agent_type": "coder"}]}\n```'
        graph = OrchestratorRunner._parse_task_graph(text, "obj")
        assert graph.objective == "obj"
        assert len(graph.tasks) == 1
        assert graph.tasks[0].task_id == "t1"

    def test_parse_json_without_fences(self):
        text = '{"tasks": [{"task_id": "t1", "description": "d", "agent_type": "coder"}]}'
        graph = OrchestratorRunner._parse_task_graph(text, "obj")
        assert len(graph.tasks) == 1

    def test_parse_with_expected_artifacts(self):
        text = '{"tasks": [{"task_id": "t1", "description": "d", "agent_type": "coder", "expected_artifacts": ["a.py", "b.py"]}]}'
        graph = OrchestratorRunner._parse_task_graph(text, "obj")
        assert graph.tasks[0].expected_artifacts == ["a.py", "b.py"]

    def test_parse_with_dependencies(self):
        text = '{"tasks": [{"task_id": "t1", "description": "d", "agent_type": "coder"}, {"task_id": "t2", "description": "d2", "agent_type": "coder", "dependencies": ["t1"]}]}'
        graph = OrchestratorRunner._parse_task_graph(text, "obj")
        assert graph.tasks[1].dependencies == ["t1"]

    def test_parse_with_input_context(self):
        text = '{"tasks": [{"task_id": "t1", "description": "d", "agent_type": "coder", "input_context": "write hello.py with argparse"}]}'
        graph = OrchestratorRunner._parse_task_graph(text, "obj")
        assert graph.tasks[0].input_context == "write hello.py with argparse"

    def test_parse_invalid_json_raises(self):
        with pytest.raises(ValueError):
            OrchestratorRunner._parse_task_graph("not json", "obj")

    def test_parse_no_json_object_raises(self):
        with pytest.raises(ValueError):
            OrchestratorRunner._parse_task_graph("just some text", "obj")


class TestSpawnAgentBuildInput:
    """Test SpawnAgentTool._build_input composes correct context."""

    def test_basic_task(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "write hello.py", "coder", input_context="use print")],
        ))
        task = board.get_task("t1")
        inp = SpawnAgentTool._build_input(task, board)
        assert "write hello.py" in inp
        assert "use print" in inp
        assert "check_messages" in inp  # Communication reminder

    def test_with_dependencies(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[
                TaskNode("t1", "design", "coder", expected_artifacts=["api.yml"]),
                TaskNode("t2", "implement", "coder", dependencies=["t1"]),
            ],
        ))
        board.update_task("t1", status=TaskStatus.COMPLETED, actual_artifacts=["api.yml"])

        task = board.get_task("t2")
        inp = SpawnAgentTool._build_input(task, board)
        assert "Upstream artifacts" in inp
        assert "api.yml" in inp

    def test_with_pending_messages(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "fix bug", "coder")],
        ))
        board.send_mail("reviewer", "coder", "check line 42")

        task = board.get_task("t1")
        inp = SpawnAgentTool._build_input(task, board)
        assert "Messages from other agents" in inp
        assert "check line 42" in inp

    def test_without_messages_no_empty_section(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))

        task = board.get_task("t1")
        inp = SpawnAgentTool._build_input(task, board)
        # Should NOT have "Messages from other agents" section when empty
        assert inp.count("Messages from other agents") == 0
        # But should still have communication reminder
        assert "check_messages" in inp


class TestArtifactExtraction:
    """Test SpawnAgentTool._extract_artifacts."""

    def test_files_created(self):
        output = "FILES_CREATED: a.py, b.py\nSUMMARY: done"
        arts = SpawnAgentTool._extract_artifacts(output)
        assert "a.py" in arts
        assert "b.py" in arts

    def test_files_modified(self):
        output = "FILES_MODIFIED: config.py\nSUMMARY: updated"
        arts = SpawnAgentTool._extract_artifacts(output)
        assert "config.py" in arts

    def test_no_markers_no_paths(self):
        output = "I completed the task. Everything works fine."
        arts = SpawnAgentTool._extract_artifacts(output)
        assert arts == []

    def test_inline_paths(self):
        output = "Created src/main.py and tests/test_main.py"
        arts = SpawnAgentTool._extract_artifacts(output)
        assert "src/main.py" in arts
        assert "tests/test_main.py" in arts

    def test_dedup(self):
        output = "FILES_CREATED: a.py\nFILES_CREATED: a.py"
        arts = SpawnAgentTool._extract_artifacts(output)
        assert arts == ["a.py"]


def test_coder_input_requires_creating_missing_target_package():
    from openagents_orchestration.models.task import TaskNode
    from openagents_orchestration.core.state_board import StateBoard
    from openagents_orchestration.tools.director.spawn_agent import SpawnAgentTool

    board = StateBoard("obj", echo=False)
    task = TaskNode("t1", "Build a package under webhook_eval_full", "coder")

    input_text = SpawnAgentTool._build_input(task, board)

    assert "empty directory is NOT a blocker" in input_text
    assert "CREATE the requested package" in input_text
    assert "NOT a reason to ask_human" in input_text
