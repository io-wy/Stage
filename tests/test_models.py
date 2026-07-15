"""Adversarial + contract tests for the data models (task / delivery / pattern).

Module under test:
- ``models/task.py`` (TaskGraph DAG validation, topological layers, serialization)
- ``models/delivery.py`` (DeliveryReport)
- ``models/pattern.py`` (PatternOutcome, FailureDecision)
"""

from __future__ import annotations

import pytest

from openagents_orchestration.models.pattern import (
    FailureDecision,
    FailureGrade,
    PatternError,
    PatternOutcome,
    PatternOutcomeStatus,
)
from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus


def _node(tid: str, deps: list[str] | None = None) -> TaskNode:
    return TaskNode(task_id=tid, description=tid, agent_type="coder", dependencies=deps or [])


# ── TaskGraph: validation that works ──────────────────────────────────────────


def test_validate_accepts_valid_dag_and_layers_are_ordered():
    g = TaskGraph("obj", [_node("a"), _node("b", ["a"]), _node("c", ["a", "b"])])
    g.validate()  # no raise
    layers = g.topological_layers()
    assert [n.task_id for n in layers[0]] == ["a"]
    assert {n.task_id for n in layers[1]} == {"b"}
    assert {n.task_id for n in layers[2]} == {"c"}


def test_validate_rejects_unknown_dependency():
    g = TaskGraph("obj", [_node("a", ["ghost"])])
    with pytest.raises(ValueError, match="unknown task"):
        g.validate()


def test_validate_rejects_two_node_cycle():
    g = TaskGraph("obj", [_node("a", ["b"]), _node("b", ["a"])])
    with pytest.raises(ValueError, match="circular"):
        g.validate()


def test_validate_rejects_self_dependency():
    g = TaskGraph("obj", [_node("a", ["a"])])
    with pytest.raises(ValueError, match="circular"):
        g.validate()


def test_is_ready_and_is_terminal():
    n = _node("a", ["dep1", "dep2"])
    assert n.is_ready({"dep1"}) is False
    assert n.is_ready({"dep1", "dep2"}) is True
    assert n.is_terminal() is False
    n.status = TaskStatus.COMPLETED
    assert n.is_terminal() is True


# ── GAP: duplicate task_id is misdiagnosed and silently drops a task ──────────


def test_gap_duplicate_task_id_with_no_edges_reported_as_cycle():
    """``_has_cycle`` returns ``visited != len(self.tasks)``, but ``visited``
    counts UNIQUE ids (in_degree/adj are dicts keyed by task_id) while
    ``len(self.tasks)`` counts the raw list. With a duplicate id the two
    disagree, so a graph of two identically-named tasks and ZERO dependencies is
    reported as having 'circular dependencies' — doubly wrong: there are no edges
    to form a cycle, and the real fault (a duplicate id) is never named."""
    g = TaskGraph("obj", [_node("dup"), _node("dup")])
    with pytest.raises(ValueError, match="circular"):
        g.validate()


def test_gap_duplicate_task_id_silently_dropped_in_layers():
    """If a caller skips validate() (or catches its error), ``topological_layers``
    keys ``remaining`` by task_id, so duplicate-id tasks collapse and one is
    silently dropped — the graph schedules fewer tasks than were defined."""
    g = TaskGraph("obj", [_node("dup"), _node("dup")])
    layers = g.topological_layers()
    flat = [n.task_id for layer in layers for n in layer]
    assert flat == ["dup"]  # GAP: 2 tasks defined, only 1 scheduled


# ── TaskNode / TaskGraph serialization ────────────────────────────────────────


def test_tasknode_roundtrip_with_subgraph():
    parent = _node("p")
    parent.subgraph = TaskGraph("child", [_node("x")])
    r = TaskNode.from_dict(parent.to_dict())
    assert r.subgraph is not None
    assert r.subgraph.get_task("x") is not None


def test_tasknode_roundtrip_with_subtasks():
    parent = _node("p")
    parent.subtasks = [_node("s1"), _node("s2", ["s1"])]
    d = parent.to_dict()
    assert "subtasks" in d
    assert len(d["subtasks"]) == 2
    r = TaskNode.from_dict(d)
    assert len(r.subtasks) == 2
    assert r.subtasks[0].task_id == "s1"
    assert r.subtasks[1].dependencies == ["s1"]


def test_gap_tasknode_from_dict_keyerror_on_missing_required_field():
    """``from_dict`` reads ``data['task_id']`` / ``['description']`` /
    ``['agent_type']`` directly while every other field uses ``.get`` with a
    default — so a partial/migrated record raises a raw KeyError."""
    with pytest.raises(KeyError):
        TaskNode.from_dict({"task_id": "a"})  # missing description, agent_type


# ── DeliveryReport success accounting removed ─────────────────────────────────
# success_rate / all_succeeded were deleted as meaningless for open-ended tasks.


# ── PatternOutcome contract ───────────────────────────────────────────────────


def test_pattern_outcome_defaults_to_completed():
    outcome = PatternOutcome(output="done")
    assert outcome.status == PatternOutcomeStatus.COMPLETED
    assert outcome.is_success is True
    assert outcome.is_terminal is True


def test_pattern_outcome_failed_is_terminal_not_success():
    err = PatternError(message="boom", grade=FailureGrade.AGENT_FATAL)
    outcome = PatternOutcome(status=PatternOutcomeStatus.FAILED, error=err)
    assert outcome.is_terminal is True
    assert outcome.is_success is False
    assert outcome.error.grade == FailureGrade.AGENT_FATAL


def test_pattern_outcome_awaiting_human_is_not_terminal():
    outcome = PatternOutcome(status=PatternOutcomeStatus.AWAITING_HUMAN)
    assert outcome.is_terminal is False
    assert outcome.is_success is False


def test_pattern_outcome_to_dict_roundtrip():
    err = PatternError(
        message="bad params",
        grade=FailureGrade.RECOVERABLE,
        tool_id="edit_file",
        details={"path": "x.py"},
    )
    outcome = PatternOutcome(
        output="",
        status=PatternOutcomeStatus.FAILED,
        error=err,
        metadata={"steps_used": 3},
    )
    d = outcome.to_dict()
    assert d["status"] == "failed"
    assert d["error"]["grade"] == "recoverable"
    assert d["error"]["tool_id"] == "edit_file"
    assert d["metadata"]["steps_used"] == 3


def test_failure_decision_defaults():
    decision = FailureDecision(action="retry", reason="transient")
    assert decision.max_retries == 3
    assert decision.delay is None
