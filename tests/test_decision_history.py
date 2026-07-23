"""Tests for DecisionHistory — Director feedback loop."""
from __future__ import annotations

from openagents_orchestration.runtime.decision_history import (
    DecisionHistory,
    DecisionRecord,
)


class TestDecisionHistory:
    def test_empty(self):
        dh = DecisionHistory()
        assert len(dh) == 0
        assert dh.summary() == {"total": 0}
        assert dh.recent() == []

    def test_record_and_recent(self):
        dh = DecisionHistory(max_decisions=10)
        dh.record(DecisionRecord(
            decision_type="spawn_agent",
            task_id="t1",
            agent_id="coder-t1",
            agent_type="coder",
            reasoning="need someone to code",
            outcome="completed",
            artifacts_produced=["main.py"],
            token_spent=1500,
        ))
        assert len(dh) == 1

        recent = dh.recent(5)
        assert len(recent) == 1
        assert recent[0]["decision"] == "spawn_agent"
        assert recent[0]["outcome"] == "completed"
        assert recent[0]["task"] == "t1"

    def test_summary(self):
        dh = DecisionHistory()
        dh.record(DecisionRecord(decision_type="spawn_agent", task_id="t1", outcome="completed"))
        dh.record(DecisionRecord(decision_type="spawn_agent", task_id="t2", outcome="failed", error="timeout"))
        dh.record(DecisionRecord(decision_type="replan", task_id="t3", outcome="completed"))

        s = dh.summary()
        assert s["total_decisions"] == 3
        assert s["completed"] == 2
        assert s["failed"] == 1
        assert s["success_rate"] == round(2 / 3, 2)
        assert s["by_type"]["spawn_agent"]["total"] == 2
        assert s["by_type"]["replan"]["total"] == 1

    def test_capacity(self):
        dh = DecisionHistory(max_decisions=3)
        for i in range(5):
            dh.record(DecisionRecord(decision_type="spawn_agent", task_id=f"t{i}"))
        assert len(dh) == 3  # bounded

        recent = dh.recent(10)
        assert recent[0]["task"] == "t2"
        assert recent[-1]["task"] == "t4"

    def test_to_dict_fields(self):
        dh = DecisionHistory()
        dh.record(DecisionRecord(
            decision_type="replan",
            task_id="t99",
            reasoning="too complex, splitting",
            outcome="pending",
            token_spent=0,
        ))
        d = dh.recent(1)[0]
        assert d["decision"] == "replan"
        assert d["why"] == "too complex, splitting"
        assert d["token_spent"] == 0
        assert "error" in d
