"""HumanChannel — bidirectional human-in-the-loop gateway.

Supports:
- Agents ask humans questions (blocking / async)
- Humans proactively post messages to projects/teams/agents
- Full conversation history with project/team scoping
- Integration with StateBoard for Director visibility
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class HumanQuestion:
    """A single question from an agent to a human."""

    qid: str
    project_id: str
    from_agent: str
    question: str
    options: str = ""
    answer: str | None = None
    team_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = None

    @property
    def is_answered(self) -> bool:
        return self.answer is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "project_id": self.project_id,
            "team_id": self.team_id,
            "from_agent": self.from_agent,
            "question": self.question,
            "options": self.options,
            "answer": self.answer,
            "created_at": self.created_at.isoformat(),
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HumanQuestion:
        answered_at = data.get("answered_at")
        return cls(
            qid=data.get("qid", ""),
            project_id=data.get("project_id", ""),
            from_agent=data.get("from_agent", ""),
            question=data.get("question", ""),
            options=data.get("options", ""),
            answer=data.get("answer"),
            team_id=data.get("team_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
            answered_at=datetime.fromisoformat(answered_at) if answered_at else None,
        )


@dataclass
class HumanMessage:
    """A proactive message posted by a human."""

    msg_id: str
    from_human: str
    content: str
    project_id: str = ""
    team_id: str = ""
    target_agent: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "from_human": self.from_human,
            "content": self.content,
            "project_id": self.project_id,
            "team_id": self.team_id,
            "target_agent": self.target_agent,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HumanMessage:
        return cls(
            msg_id=data.get("msg_id", ""),
            from_human=data.get("from_human", ""),
            content=data.get("content", ""),
            project_id=data.get("project_id", ""),
            team_id=data.get("team_id", ""),
            target_agent=data.get("target_agent", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


class HumanChannel:
    """Gateway for human-agent bidirectional communication.

    Current implementation is single-process, in-memory only.

    TODO: For multi-instance deployments, back this with a shared store
    (Redis, PostgreSQL).  The ``ask`` / ``answer`` / ``post_message``
    methods are designed so that a ``RedisHumanChannel`` subclass could
    override just the storage layer while keeping the same interface.
    """

    def __init__(self) -> None:
        self._questions: dict[str, HumanQuestion] = {}
        self._messages: list[HumanMessage] = []

    # -- questions -------------------------------------------------------------

    def ask(
        self,
        project_id: str,
        from_agent: str,
        question: str,
        *,
        team_id: str | None = None,
        options: str = "",
    ) -> str:
        """Record a question from an agent.  Returns the question ID."""
        qid = f"hq-{uuid.uuid4().hex[:8]}"
        hq = HumanQuestion(
            qid=qid,
            project_id=project_id,
            from_agent=from_agent,
            question=question,
            options=options,
            team_id=team_id,
        )
        self._questions[qid] = hq
        return qid

    def answer(self, qid: str, answer: str) -> bool:
        """Record a human answer.  Returns True if the question existed and was unanswered."""
        hq = self._questions.get(qid)
        if hq is None or hq.is_answered:
            return False
        hq.answer = answer
        hq.answered_at = datetime.now(UTC)
        return True

    def get_pending_questions(
        self,
        project_id: str | None = None,
        team_id: str | None = None,
    ) -> list[HumanQuestion]:
        """Return unanswered questions, optionally filtered by project/team."""
        results: list[HumanQuestion] = []
        for hq in self._questions.values():
            if hq.is_answered:
                continue
            if project_id is not None and hq.project_id != project_id:
                continue
            if team_id is not None and hq.team_id != team_id:
                continue
            results.append(hq)
        return results

    def get_answered_questions(
        self,
        project_id: str | None = None,
        team_id: str | None = None,
    ) -> list[HumanQuestion]:
        """Return answered questions, optionally filtered."""
        results: list[HumanQuestion] = []
        for hq in self._questions.values():
            if not hq.is_answered:
                continue
            if project_id is not None and hq.project_id != project_id:
                continue
            if team_id is not None and hq.team_id != team_id:
                continue
            results.append(hq)
        return results

    def get_question(self, qid: str) -> HumanQuestion | None:
        return self._questions.get(qid)

    # -- proactive messages ----------------------------------------------------

    def post_message(
        self,
        from_human: str,
        content: str,
        *,
        project_id: str = "",
        team_id: str = "",
        target_agent: str = "",
    ) -> str:
        """Record a proactive human message.  Returns the message ID."""
        msg_id = f"hm-{uuid.uuid4().hex[:8]}"
        self._messages.append(
            HumanMessage(
                msg_id=msg_id,
                from_human=from_human,
                content=content,
                project_id=project_id,
                team_id=team_id,
                target_agent=target_agent,
            )
        )
        return msg_id

    def get_messages(
        self,
        *,
        project_id: str | None = None,
        team_id: str | None = None,
        target_agent: str | None = None,
        since: datetime | None = None,
    ) -> list[HumanMessage]:
        """Return human messages, optionally filtered."""
        results: list[HumanMessage] = []
        for msg in self._messages:
            if project_id is not None and msg.project_id != project_id:
                continue
            if team_id is not None and msg.team_id != team_id:
                continue
            if target_agent is not None and msg.target_agent != target_agent:
                continue
            if since is not None and msg.created_at < since:
                continue
            results.append(msg)
        return results

    # -- combined activity -----------------------------------------------------

    def get_activity(
        self,
        *,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return combined human activity (questions + messages) sorted by time."""
        items: list[dict[str, Any]] = []
        for hq in self._questions.values():
            if project_id is not None and hq.project_id != project_id:
                continue
            items.append({
                "type": "ask",
                "qid": hq.qid,
                "from_agent": hq.from_agent,
                "question": hq.question,
                "answer": hq.answer,
                "created_at": hq.created_at,
                "project_id": hq.project_id,
                "team_id": hq.team_id,
            })
        for msg in self._messages:
            if project_id is not None and msg.project_id != project_id:
                continue
            items.append({
                "type": "post",
                "msg_id": msg.msg_id,
                "from_human": msg.from_human,
                "content": msg.content,
                "created_at": msg.created_at,
                "project_id": msg.project_id,
                "team_id": msg.team_id,
                "target_agent": msg.target_agent,
            })
        items.sort(key=lambda x: x["created_at"])
        return items[-limit:]

    # -- serialization ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions": [q.to_dict() for q in self._questions.values()],
            "messages": [m.to_dict() for m in self._messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HumanChannel:
        ch = cls()
        for q in data.get("questions", []):
            hq = HumanQuestion.from_dict(q)
            ch._questions[hq.qid] = hq
        for m in data.get("messages", []):
            ch._messages.append(HumanMessage.from_dict(m))
        return ch
