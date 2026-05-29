"""EventReplayer — replay persisted events onto a StateBoard.

After loading a snapshot, replay events that occurred after the snapshot
to bring the board to the latest known state.
"""

from __future__ import annotations

from typing import Any


class EventReplayer:
    """Replay persisted mutation events onto a StateBoard.

    Each event type maps to a StateBoard method call.  Events that cannot
    be replayed (e.g. tasks.imported without a TaskGraph) are skipped with
    a warning.
    """

    def replay(self, board: Any, events: list[dict[str, Any]]) -> None:
        """Replay a list of events onto the board."""
        for event in events:
            try:
                self._replay_one(board, event)
            except Exception as exc:
                # Log and continue — don't let one bad event break the whole resume
                event_type = event.get("type", "unknown")
                import sys
                print(
                    f"[EventReplayer] Skipped {event_type}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

    def _replay_one(self, board: Any, event: dict[str, Any]) -> None:
        event_type = event.get("type", "")
        task_id = event.get("task_id")
        agent_id = event.get("agent_id")
        message = event.get("message", "")
        payload = event.get("payload", {})
        # Fields may be at top level (recorder flattens kwargs) or in payload
        fields = event.get("fields", payload.get("fields", {}))

        # -- task events -------------------------------------------------------
        if event_type == "tasks.imported":
            # Cannot replay without TaskGraph — snapshot already has tasks
            pass

        elif event_type == "task.added":
            # Cannot replay without TaskNode — snapshot already has tasks
            pass

        elif event_type.startswith("task."):
            # task.running, task.completed, task.failed, task.skipped
            if fields:
                board.update_task(task_id, **fields)
            elif "status=" in message:
                # Fallback: parse status from message
                status = event_type.split(".", 1)[1]
                board.update_task(task_id, status=status)

        # -- agent events ------------------------------------------------------
        elif event_type == "agent.registered":
            # Parse agent_type from message: "Registered {agent_type}"
            agent_type = message.replace("Registered ", "") if message.startswith("Registered ") else "coder"
            if agent_id and agent_id not in board.agents:
                board.register_agent(agent_id, agent_type)

        elif event_type.startswith("agent."):
            # agent.running, agent.done, agent.failed, etc.
            if fields:
                board.update_agent(agent_id, **fields)

        # -- resident events ---------------------------------------------------
        elif event_type == "resident.registered":
            # Cannot fully replay without ResidentState — but snapshot has it
            pass

        elif event_type.startswith("resident."):
            if fields:
                board.update_resident(agent_id, **fields)

        # -- artifact events ---------------------------------------------------
        elif event_type == "artifact.claimed":
            # Parse paths from message: "N artifact(s) by {task_id}: [paths]"
            # Or from payload
            paths = payload.get("paths", [])
            if paths and task_id:
                board.claim_artifact(task_id, paths)

        elif event_type == "artifact.verified":
            board.verify_artifact(message, exists=True)

        elif event_type == "artifact.missing":
            board.verify_artifact(message, exists=False)

        # -- budget events -----------------------------------------------------
        elif event_type == "budget.tokens":
            # Recorder flattens kwargs to top level
            n = event.get("n", payload.get("n", 0))
            if n > 0:
                board.add_tokens(n)

        elif event_type == "budget.steps":
            n = event.get("n", payload.get("n", 0))
            if n > 0:
                board.add_steps(n)

        # -- mail events -------------------------------------------------------
        elif event_type == "mail.sent":
            to_id = event.get("to_id", payload.get("to_id"))
            content = event.get("content", payload.get("content"))
            if to_id and content and agent_id:
                board.send_mail(agent_id, to_id, content)

        elif event_type == "mail.cleared":
            recipient = event.get("recipient", payload.get("recipient"))
            board.clear_mail(recipient)

        # -- human events ------------------------------------------------------
        elif event_type == "human.asked":
            question = event.get("question", payload.get("question", message))
            options = event.get("options", payload.get("options", ""))
            from_agent = event.get("from_agent", payload.get("from_agent", agent_id or ""))
            board.ask_human(question, options=options, from_agent=from_agent)

        elif event_type == "human.replied":
            qid = event.get("qid", payload.get("qid"))
            answer = event.get("answer", payload.get("answer"))
            if qid and answer:
                board.reply_human(qid, answer)
