"""Matrix adapter for OpenAgents orchestrator.

Usage:
    export MATRIX_HOMESERVER=https://matrix.example.com
    export MATRIX_USER_ID=@bot:example.com
    export MATRIX_ACCESS_TOKEN=...
    export MATRIX_WORK_DIR=./matrix_work
    uv run python scripts/run_matrix_bot.py

Each Matrix room gets its own project. The bot responds to text messages and
forwards `ask_human` questions back to the room.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from nio import AsyncClient, MatrixRoom, RoomMessageText

from openagents_orchestration.core.state_board import Budget
from openagents_orchestration.projects.global_orchestrator import (
    GlobalOrchestrator,
)


class MatrixAdapter:
    """Bridge Matrix rooms to the orchestrator.

    Each room is treated as an isolated project. Messages from room members are
    passed as objectives to ``GlobalOrchestrator.run``. Replies and human
    questions are posted back to the room.
    """

    def __init__(
        self,
        homeserver: str,
        user_id: str,
        access_token: str,
        *,
        config_path: str | Path = "agent.json",
        work_dir: str | Path = ".",
        token_limit: int = 100_000,
        max_steps: int = 50,
        question_poll_interval_s: float = 5.0,
    ) -> None:
        self._client = AsyncClient(homeserver, user_id)
        self._client.access_token = access_token

        self._orchestrator = GlobalOrchestrator(
            Path(config_path),
            persist_dir=None,
            enable_monitor=False,
        )
        self._work_root = Path(work_dir)
        self._budget = Budget(
            token_limit=token_limit,
            time_limit_s=1800.0,
            max_steps=max_steps,
        )
        self._question_poll_interval_s = question_poll_interval_s
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start listening to Matrix events and polling for human questions."""
        self._client.add_event_callback(self._on_message, RoomMessageText)
        asyncio.create_task(self._poll_human_questions())
        await self._client.sync_forever(timeout=30000, full_state=False)

    async def stop(self) -> None:
        """Close the Matrix client and shut down the orchestrator."""
        await self._client.close()
        await self._orchestrator.shutdown()

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        """Handle an incoming text message in a Matrix room."""
        if event.sender == self._client.user_id:
            return

        room_id = room.room_id
        body = event.body.strip()
        if not body:
            return

        async with self._lock:
            pending = self._orchestrator.human_channel.get_pending_questions(
                project_id=room_id
            )
            if pending:
                hq = pending[-1]
                if self._orchestrator.human_channel.answer(hq.qid, body):
                    await self._send_text(room_id, "Answer recorded.")
                    return

        await self._send_text(room_id, f"Running: {body[:200]}...")
        try:
            report = await self._orchestrator.run(
                objective=body,
                budget=self._budget,
                work_dir=str(self._room_work_dir(room_id)),
                project_id=room_id,
            )
            text = report.final_output or "Done."
            await self._send_text(room_id, text[:4000])
        except Exception as exc:  # noqa: BLE001
            await self._send_text(room_id, f"Error: {exc}")

    async def _poll_human_questions(self) -> None:
        """Periodically check for unanswered human questions and post them."""
        while True:
            await asyncio.sleep(self._question_poll_interval_s)
            try:
                questions = self._orchestrator.human_channel.get_pending_questions()
                for hq in questions:
                    text = f"Question from {hq.from_agent}:\n{hq.question}"
                    await self._send_text(hq.project_id, text[:4000])
            except Exception:  # noqa: BLE001
                # Polling must not crash the bot.
                pass

    async def _send_text(self, room_id: str, text: str) -> None:
        """Send a plain-text message to a Matrix room."""
        await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": text},
        )

    def _room_work_dir(self, room_id: str) -> Path:
        """Return a filesystem-safe work directory for a room."""
        safe = hashlib.sha256(room_id.encode()).hexdigest()[:16]
        path = self._work_root / safe
        path.mkdir(parents=True, exist_ok=True)
        return path


async def main() -> None:
    """CLI entry point for the Matrix bot."""
    homeserver = os.environ.get("MATRIX_HOMESERVER", "").strip()
    user_id = os.environ.get("MATRIX_USER_ID", "").strip()
    access_token = os.environ.get("MATRIX_ACCESS_TOKEN", "").strip()

    if not homeserver or not user_id or not access_token:
        raise SystemExit(
            "Set MATRIX_HOMESERVER, MATRIX_USER_ID, and MATRIX_ACCESS_TOKEN"
        )

    adapter = MatrixAdapter(
        homeserver=homeserver,
        user_id=user_id,
        access_token=access_token,
        config_path=Path(__file__).parent.parent / "agent.json",
        work_dir=os.environ.get("MATRIX_WORK_DIR", "./matrix_work"),
        token_limit=int(os.environ.get("MATRIX_TOKEN_LIMIT", "100000")),
        max_steps=int(os.environ.get("MATRIX_MAX_STEPS", "50")),
    )
    try:
        await adapter.start()
    finally:
        await adapter.stop()


if __name__ == "__main__":
    asyncio.run(main())
