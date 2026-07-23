"""Tests for HumanChannel."""

from __future__ import annotations

from datetime import UTC, datetime

from openagents_orchestration.runtime.human_channel import (
    HumanChannel,
    HumanMessage,
    HumanQuestion,
)

# ── ask / answer basics ───────────────────────────────────────────────────────


def test_ask_returns_qid_and_question_in_pending():
    """ask() returns a qid and the question appears in pending list."""
    ch = HumanChannel()
    qid = ch.ask(
        project_id="proj-1",
        from_agent="agent-alpha",
        question="What is the database password?",
        team_id="team-x",
    )

    assert isinstance(qid, str)
    assert qid.startswith("hq-")

    pending = ch.get_pending_questions()
    assert len(pending) == 1
    assert pending[0].qid == qid
    assert pending[0].question == "What is the database password?"
    assert pending[0].from_agent == "agent-alpha"
    assert pending[0].project_id == "proj-1"
    assert pending[0].team_id == "team-x"
    assert pending[0].answer is None
    assert not pending[0].is_answered


def test_answer_moves_question_to_answered():
    """answer() moves a question from pending to answered."""
    ch = HumanChannel()
    qid = ch.ask(
        project_id="proj-1",
        from_agent="agent-beta",
        question="Should we use Redis?",
    )

    ok = ch.answer(qid, "Yes, for caching layer.")
    assert ok is True

    pending = ch.get_pending_questions()
    assert len(pending) == 0

    answered = ch.get_answered_questions()
    assert len(answered) == 1
    assert answered[0].qid == qid
    assert answered[0].answer == "Yes, for caching layer."
    assert answered[0].is_answered
    assert answered[0].answered_at is not None


# ── filtering by project_id / team_id ─────────────────────────────────────────


def test_get_pending_filter_by_project():
    """get_pending_questions filters correctly by project_id."""
    ch = HumanChannel()
    ch.ask(project_id="p1", from_agent="a1", question="Q1")
    ch.ask(project_id="p2", from_agent="a2", question="Q2")

    p1_pending = ch.get_pending_questions(project_id="p1")
    assert len(p1_pending) == 1
    assert p1_pending[0].question == "Q1"

    p2_pending = ch.get_pending_questions(project_id="p2")
    assert len(p2_pending) == 1
    assert p2_pending[0].question == "Q2"

    all_pending = ch.get_pending_questions()
    assert len(all_pending) == 2


def test_get_pending_filter_by_team():
    """get_pending_questions filters correctly by team_id."""
    ch = HumanChannel()
    ch.ask(project_id="p1", from_agent="a1", question="Q1", team_id="t1")
    ch.ask(project_id="p1", from_agent="a2", question="Q2", team_id="t2")
    ch.ask(project_id="p1", from_agent="a3", question="Q3")  # no team

    t1_pending = ch.get_pending_questions(team_id="t1")
    assert len(t1_pending) == 1
    assert t1_pending[0].question == "Q1"

    no_team = ch.get_pending_questions(team_id=None)
    assert len(no_team) == 3  # None means no filter


def test_get_answered_filter_by_project():
    """get_answered_questions filters correctly by project_id."""
    ch = HumanChannel()
    q1 = ch.ask(project_id="p1", from_agent="a1", question="Q1")
    q2 = ch.ask(project_id="p2", from_agent="a2", question="Q2")
    ch.answer(q1, "A1")
    ch.answer(q2, "A2")

    p1_answered = ch.get_answered_questions(project_id="p1")
    assert len(p1_answered) == 1
    assert p1_answered[0].question == "Q1"


# ── post_message / get_messages ────────────────────────────────────────────────


def test_post_message_and_get_messages():
    """post_message returns msg_id; get_messages filters correctly."""
    ch = HumanChannel()
    msg_id = ch.post_message(
        from_human="alice",
        content="Deploy to staging now",
        project_id="p1",
        team_id="t1",
        target_agent="deploy-agent",
    )

    assert isinstance(msg_id, str)
    assert msg_id.startswith("hm-")

    all_msgs = ch.get_messages()
    assert len(all_msgs) == 1
    assert all_msgs[0].content == "Deploy to staging now"
    assert all_msgs[0].from_human == "alice"
    assert all_msgs[0].project_id == "p1"
    assert all_msgs[0].team_id == "t1"
    assert all_msgs[0].target_agent == "deploy-agent"


def test_get_messages_filter_by_project_team_agent():
    """get_messages respects project_id, team_id, and target_agent filters."""
    ch = HumanChannel()
    ch.post_message(from_human="h1", content="M1", project_id="p1", team_id="t1", target_agent="a1")
    ch.post_message(from_human="h2", content="M2", project_id="p2", team_id="t1", target_agent="a1")
    ch.post_message(from_human="h3", content="M3", project_id="p1", team_id="t2", target_agent="a2")

    p1_msgs = ch.get_messages(project_id="p1")
    assert len(p1_msgs) == 2
    assert {m.content for m in p1_msgs} == {"M1", "M3"}

    t1_msgs = ch.get_messages(team_id="t1")
    assert len(t1_msgs) == 2
    assert {m.content for m in t1_msgs} == {"M1", "M2"}

    a1_msgs = ch.get_messages(target_agent="a1")
    assert len(a1_msgs) == 2
    assert {m.content for m in a1_msgs} == {"M1", "M2"}


def test_get_messages_filter_by_since():
    """get_messages respects the since timestamp filter."""
    import time

    ch = HumanChannel()
    before = datetime.now(UTC)
    ch.post_message(from_human="h1", content="Old")
    time.sleep(0.01)
    after = datetime.now(UTC)
    time.sleep(0.01)
    ch.post_message(from_human="h2", content="New")

    old_msgs = ch.get_messages(since=before)
    assert len(old_msgs) == 2

    new_msgs = ch.get_messages(since=after)
    assert len(new_msgs) == 1
    assert new_msgs[0].content == "New"


# ── duplicate answer ──────────────────────────────────────────────────────────


def test_duplicate_answer_returns_false():
    """Answering an already-answered question returns False."""
    ch = HumanChannel()
    qid = ch.ask(project_id="p1", from_agent="a1", question="Q?")
    ok1 = ch.answer(qid, "First")
    assert ok1 is True

    ok2 = ch.answer(qid, "Second")
    # Production code returns False for already-answered questions
    assert ok2 is False

    answered = ch.get_answered_questions()
    assert len(answered) == 1
    assert answered[0].answer == "First"


# ── answer nonexistent qid ────────────────────────────────────────────────────


def test_answer_nonexistent_qid_returns_false():
    """Answering a non-existent qid returns False."""
    ch = HumanChannel()
    ok = ch.answer("hq-doesnotexist", "some answer")
    assert ok is False


# ── serialization round-trip ──────────────────────────────────────────────────


def test_to_dict_from_dict_roundtrip():
    """to_dict / from_dict preserves all data."""
    ch = HumanChannel()
    q1 = ch.ask(
        project_id="p1",
        from_agent="a1",
        question="Q1?",
        team_id="t1",
        options="yes/no",
    )
    ch.answer(q1, "yes")

    ch.post_message(
        from_human="alice",
        content="Hello",
        project_id="p1",
        team_id="t1",
        target_agent="a1",
    )

    d = ch.to_dict()
    restored = HumanChannel.from_dict(d)

    # Questions
    assert len(restored._questions) == 1
    rq = restored.get_question(q1)
    assert rq is not None
    assert rq.question == "Q1?"
    assert rq.from_agent == "a1"
    assert rq.project_id == "p1"
    assert rq.team_id == "t1"
    assert rq.options == "yes/no"
    assert rq.answer == "yes"
    assert rq.is_answered

    # Messages
    assert len(restored._messages) == 1
    assert restored._messages[0].content == "Hello"
    assert restored._messages[0].from_human == "alice"
    assert restored._messages[0].project_id == "p1"


def test_question_to_dict_from_dict():
    """HumanQuestion.to_dict / from_dict round-trip."""
    now = datetime.now(UTC)
    hq = HumanQuestion(
        qid="hq-123",
        project_id="p1",
        from_agent="a1",
        question="Q?",
        options="a/b",
        answer="a",
        team_id="t1",
        created_at=now,
        answered_at=now,
    )
    d = hq.to_dict()
    restored = HumanQuestion.from_dict(d)

    assert restored.qid == hq.qid
    assert restored.question == hq.question
    assert restored.answer == hq.answer
    assert restored.is_answered
    assert restored.created_at == now
    assert restored.answered_at == now


def test_message_to_dict_from_dict():
    """HumanMessage.to_dict / from_dict round-trip."""
    now = datetime.now(UTC)
    hm = HumanMessage(
        msg_id="hm-123",
        from_human="bob",
        content="Hi",
        project_id="p2",
        team_id="t2",
        target_agent="a2",
        created_at=now,
    )
    d = hm.to_dict()
    restored = HumanMessage.from_dict(d)

    assert restored.msg_id == hm.msg_id
    assert restored.content == hm.content
    assert restored.from_human == hm.from_human
    assert restored.project_id == hm.project_id
    assert restored.team_id == hm.team_id
    assert restored.target_agent == hm.target_agent
    assert restored.created_at == now


# ── get_question ─────────────────────────────────────────────────────────────


def test_get_question():
    """get_question returns the question or None."""
    ch = HumanChannel()
    qid = ch.ask(project_id="p1", from_agent="a1", question="Q?")

    hq = ch.get_question(qid)
    assert hq is not None
    assert hq.question == "Q?"

    assert ch.get_question("nonexistent") is None


# ── get_activity ───────────────────────────────────────────────────────────────


def test_get_activity_combined_and_sorted():
    """get_activity returns both questions and messages sorted by time."""
    ch = HumanChannel()
    q1 = ch.ask(project_id="p1", from_agent="a1", question="Q1")
    ch.answer(q1, "A1")
    ch.post_message(from_human="h1", content="M1", project_id="p1")

    activity = ch.get_activity(project_id="p1")
    assert len(activity) == 2
    assert activity[0]["type"] == "ask"
    assert activity[1]["type"] == "post"


def test_get_activity_limit():
    """get_activity respects the limit parameter."""
    ch = HumanChannel()
    for i in range(5):
        ch.ask(project_id="p1", from_agent="a1", question=f"Q{i}")

    activity = ch.get_activity(project_id="p1", limit=3)
    assert len(activity) == 3


# ── edge: empty channel ───────────────────────────────────────────────────────


def test_empty_channel():
    """All getters return empty lists on a fresh channel."""
    ch = HumanChannel()
    assert ch.get_pending_questions() == []
    assert ch.get_answered_questions() == []
    assert ch.get_messages() == []
    assert ch.get_activity() == []
    assert ch.get_question("hq-xyz") is None


# ── edge: multiple questions same project ─────────────────────────────────────


def test_multiple_questions_same_project():
    """Multiple questions in same project are tracked independently."""
    ch = HumanChannel()
    q1 = ch.ask(project_id="p1", from_agent="a1", question="Q1")
    q2 = ch.ask(project_id="p1", from_agent="a2", question="Q2")

    pending = ch.get_pending_questions(project_id="p1")
    assert len(pending) == 2

    ch.answer(q1, "A1")
    pending_after = ch.get_pending_questions(project_id="p1")
    assert len(pending_after) == 1
    assert pending_after[0].qid == q2

    answered = ch.get_answered_questions(project_id="p1")
    assert len(answered) == 1
    assert answered[0].qid == q1
