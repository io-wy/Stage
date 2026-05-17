"""Tests for ask_human reply mechanism."""

from __future__ import annotations

import pytest

from openagents_orchestration.models.task import TaskGraph, TaskNode
from openagents_orchestration.state_board import StateBoard


class TestAskHumanReply:
    def test_ask_human_records_question(self):
        board = StateBoard("obj")
        qid = board.ask_human(
            "What database should we use?",
            options="Postgres, MySQL, SQLite",
            from_agent="director",
        )

        assert qid == "hq-0"
        questions = board.get_human_questions()
        assert len(questions) == 1
        assert questions[0]["question"] == "What database should we use?"
        assert questions[0]["answer"] is None

    def test_reply_human_sends_message_to_director(self):
        board = StateBoard("obj")
        qid = board.ask_human("What db?", from_agent="director")

        result = board.reply_human(qid, "Use Postgres")

        assert result is True
        assert len(board._pending_messages) == 1
        msg = board._pending_messages[0]
        assert msg["from"] == "human"
        assert msg["to"] == "director"
        assert "Use Postgres" in msg["content"]
        assert qid in msg["content"]

    def test_reply_human_returns_false_for_unknown_qid(self):
        board = StateBoard("obj")
        result = board.reply_human("hq-999", "answer")
        assert result is False

    def test_reply_human_returns_false_for_already_answered(self):
        board = StateBoard("obj")
        qid = board.ask_human("What db?", from_agent="director")
        board.reply_human(qid, "Postgres")

        # Second reply should fail
        result = board.reply_human(qid, "MySQL")
        assert result is False

    def test_get_human_questions_filtered(self):
        board = StateBoard("obj")
        q1 = board.ask_human("Q1?", from_agent="director")
        q2 = board.ask_human("Q2?", from_agent="director")
        board.reply_human(q1, "A1")

        unanswered = board.get_human_questions(answered=False)
        assert len(unanswered) == 1
        assert unanswered[0]["id"] == q2

        answered = board.get_human_questions(answered=True)
        assert len(answered) == 1
        assert answered[0]["id"] == q1

    def test_snapshot_includes_waiting_for_human(self):
        board = StateBoard("obj")
        board.add_tasks(TaskGraph(
            objective="obj",
            tasks=[TaskNode("t1", "task", "coder")],
        ))
        board.ask_human("What db?", from_agent="director")

        snapshot = board.snapshot()
        assert "waiting_for_human" in snapshot["signals"]
        assert len(snapshot["signals"]["waiting_for_human"]) == 1
        assert snapshot["signals"]["waiting_for_human"][0]["id"] == "hq-0"
