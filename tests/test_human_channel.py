"""Tests for Human-in-the-Loop bidirectional channel."""

from __future__ import annotations

import pytest

from openagents_orchestration.state_board import StateBoard


class TestHumanChannel:
    def test_ask_human_creates_question(self):
        board = StateBoard("obj", echo=False)
        qid = board.ask_human("What model?", options="A, B", from_agent="coder-t1")
        assert qid.startswith("hq-")
        questions = board.get_human_questions()
        assert len(questions) == 1
        assert questions[0]["question"] == "What model?"
        assert questions[0]["options"] == "A, B"

    def test_reply_human_records_answer(self):
        board = StateBoard("obj", echo=False)
        qid = board.ask_human("What model?", from_agent="coder-t1")
        ok = board.reply_human(qid, "GPT-4")
        assert ok is True
        questions = board.get_human_questions(answered=True)
        assert len(questions) == 1
        assert questions[0]["answer"] == "GPT-4"
        # Also delivered via mailbox to director
        msgs = board.messages_for("director")
        assert any("GPT-4" in m["content"] for m in msgs)

    def test_human_post_creates_message(self):
        board = StateBoard("obj", echo=False)
        board.human_post("alice", "Please pause and switch priority")
        assert len(board._human_messages) == 1
        assert board._human_messages[0]["content"] == "Please pause and switch priority"
        # Routed to director via mailbox
        msgs = board.messages_for("director")
        assert any("pause" in m["content"] for m in msgs)

    def test_human_post_target_team(self):
        board = StateBoard("obj", echo=False)
        board.human_post("alice", "Fix the auth bug", target_team="team-alpha")
        msgs = board.messages_for("team-alpha")
        assert len(msgs) == 1
        assert "auth bug" in msgs[0]["content"]

    def test_get_human_conversation_combined(self):
        board = StateBoard("obj", echo=False)
        board.ask_human("Q1?", from_agent="coder-t1")
        board.human_post("alice", "A1")
        conv = board.get_human_conversation()
        assert len(conv) == 2
        types = [c["type"] for c in conv]
        assert "ask" in types
        assert "post" in types

    def test_snapshot_includes_human_activity(self):
        board = StateBoard("obj", echo=False)
        board.ask_human("Q1?", from_agent="coder-t1")
        board.human_post("alice", "Switch priority")
        snap = board.snapshot()
        assert "human_activity" in snap["signals"]
        assert snap["signals"]["human_activity"]["post_count"] == 1
        assert len(snap["signals"]["human_activity"]["recent_posts"]) == 1

    def test_persistence_roundtrip(self):
        board = StateBoard("obj", echo=False)
        board.ask_human("Q1?", from_agent="coder-t1")
        board.human_post("alice", "Switch priority")
        data = board.to_dict()
        restored = StateBoard.from_dict(data, echo=False)
        assert len(restored._human_questions) == 1
        assert len(restored._human_messages) == 1
        assert restored._human_messages[0]["content"] == "Switch priority"
