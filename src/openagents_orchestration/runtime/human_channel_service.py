"""HumanChannelService — thin facade over HumanChannel for StateBoard.

This service exists to decouple human-in-the-loop storage (HumanChannel)
from the StateBoard orchestration surface. StateBoard keeps adapter methods
that delegate here, so existing callers do not need to change.
"""

from __future__ import annotations

from typing import Any

from openagents_orchestration.runtime.human_channel import HumanChannel


class HumanChannelService:
    """Gateway for human-agent bidirectional communication.

    Wraps a ``HumanChannel`` instance and provides the same public API that
    used to live directly on ``StateBoard``.
    """

    def __init__(self, human_channel: HumanChannel | None = None) -> None:
        self._human_channel = human_channel or HumanChannel()

    @property
    def channel(self) -> HumanChannel:
        """Underlying channel, exposed for direct wiring (e.g. GlobalOrchestrator)."""
        return self._human_channel

    @channel.setter
    def channel(self, value: HumanChannel) -> None:
        """Allow external wiring of a shared channel (e.g. GlobalOrchestrator)."""
        self._human_channel = value

    def human_post(
        self,
        project_id: str,
        human_id: str,
        content: str,
        *,
        target_team: str = "",
        target_agent: str = "",
    ) -> None:
        """Human proactively posts a message to a project or team."""
        self._human_channel.post_message(
            from_human=human_id,
            content=content,
            project_id=project_id,
            team_id=target_team,
            target_agent=target_agent or ("director" if not target_team else ""),
        )

    def get_human_conversation(self, project_id: str) -> list[dict[str, Any]]:
        """Return full human conversation log (asks + posts)."""
        return [
            {
                "type": item["type"],
                "from": item.get("from_agent") or item.get("from_human"),
                "content": item.get("question") or item.get("content"),
                "ts": item["created_at"].timestamp() if item.get("created_at") else 0,
            }
            for item in self._human_channel.get_activity(project_id=project_id)
        ]

    def ask_human(
        self,
        project_id: str,
        question: str,
        *,
        options: str = "",
        from_agent: str = "",
        team_id: str = "",
    ) -> str:
        """Record a question for human input. Returns a question ID."""
        return self._human_channel.ask(
            project_id=project_id,
            from_agent=from_agent,
            question=question,
            options=options,
            team_id=team_id,
        )

    def reply_human(self, qid: str, answer: str) -> bool:
        """Record a human reply. Returns True if the question was found and unanswered."""
        return self._human_channel.answer(qid, answer)

    def get_human_questions(
        self,
        project_id: str,
        *,
        answered: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return human questions. answered=None returns all."""
        if answered is None or answered is False:
            pending = self._human_channel.get_pending_questions(project_id=project_id)
        else:
            pending = []
        if answered is None or answered is True:
            answered_qs = self._human_channel.get_answered_questions(project_id=project_id)
        else:
            answered_qs = []

        result: list[dict[str, Any]] = []
        for q in pending:
            result.append({
                "id": q.qid,
                "from": q.from_agent,
                "question": q.question,
                "options": q.options,
                "answer": q.answer,
            })
        for q in answered_qs:
            result.append({
                "id": q.qid,
                "from": q.from_agent,
                "question": q.question,
                "options": q.options,
                "answer": q.answer,
            })
        return result

    def get_pending_questions(self, project_id: str) -> list[Any]:
        """Return unanswered questions for a project."""
        return self._human_channel.get_pending_questions(project_id=project_id)

    def get_messages(self, project_id: str) -> list[Any]:
        """Return human messages for a project."""
        return self._human_channel.get_messages(project_id=project_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize underlying HumanChannel state."""
        return self._human_channel.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HumanChannelService:
        """Restore from serialized HumanChannel state."""
        return cls(HumanChannel.from_dict(data))
