"""Tests for TaskGraph model."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


class TestTaskGraph:
    def test_topological_layers_linear(self):
        tg = TaskGraph(
            objective="linear",
            tasks=[
                TaskNode("t1", "A", "coder"),
                TaskNode("t2", "B", "coder", dependencies=["t1"]),
                TaskNode("t3", "C", "coder", dependencies=["t2"]),
            ],
        )
        layers = tg.topological_layers()
        assert [[t.task_id for t in layer] for layer in layers] == [["t1"], ["t2"], ["t3"]]

    def test_topological_layers_parallel(self):
        tg = TaskGraph(
            objective="parallel",
            tasks=[
                TaskNode("t1", "A", "coder"),
                TaskNode("t2", "B", "coder", dependencies=["t1"]),
                TaskNode("t3", "C", "tester", dependencies=["t1"]),
                TaskNode("t4", "D", "coder", dependencies=["t2", "t3"]),
            ],
        )
        layers = tg.topological_layers()
        assert [sorted([t.task_id for t in layer]) for layer in layers] == [
            ["t1"], ["t2", "t3"], ["t4"]
        ]

    def test_cycle_detection(self):
        tg = TaskGraph(
            objective="cycle",
            tasks=[
                TaskNode("t1", "A", "coder", dependencies=["t2"]),
                TaskNode("t2", "B", "coder", dependencies=["t1"]),
            ],
        )
        with pytest.raises(ValueError, match="circular"):
            tg.validate()

    def test_unknown_dependency(self):
        tg = TaskGraph(
            objective="unknown",
            tasks=[
                TaskNode("t1", "A", "coder", dependencies=["missing"]),
            ],
        )
        with pytest.raises(ValueError, match="unknown task"):
            tg.validate()

    def test_serialization_roundtrip(self):
        tg = TaskGraph(
            objective="roundtrip",
            tasks=[
                TaskNode("t1", "A", "coder", dependencies=[], expected_artifacts=["a.py"]),
            ],
        )
        data = tg.to_dict()
        restored = TaskGraph.from_dict(data)
        assert restored.objective == tg.objective
        assert len(restored.tasks) == 1
        assert restored.tasks[0].task_id == "t1"
