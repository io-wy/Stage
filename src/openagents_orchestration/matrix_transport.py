"""Matrix transport — optional backend for agent-to-agent messaging.

Mirrors HiClaw's Matrix room model:
- Leader Room: Director + TeamLeader
- Team Room: TeamLeader + Workers
- DM: one-to-one private

Uses matrix-nio for async C/S API. Falls back to in-memory when Matrix is not
configured so existing behaviour is unchanged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from nio import AsyncClient, RoomCreateResponse, RoomMessageText, SyncResponse

_MAX_ROOMS_CACHE = 256


@dataclass
class MatrixConfig:
    """Matrix homeserver credentials."""

    homeserver: str
    user_id: str
    access_token: str
    device_id: str = "openagents_orchestration"


class MatrixClient:
    """Thin wrapper around matrix-nio AsyncClient.

    Handles login, room creation, send/receive. One instance per agent identity.
    """

    def __init__(self, config: MatrixConfig):
        self._config = config
        self._client = AsyncClient(
            homeserver=config.homeserver,
            user=config.user_id,
            device_id=config.device_id,
        )
        self._client.access_token = config.access_token
        self._rooms: dict[str, str] = {}  # room_alias -> room_id
        self._since_token: str | None = None

    async def close(self) -> None:
        await self._client.close()

    # -- room management -----------------------------------------------------

    async def create_room(
        self,
        name: str,
        invite: list[str] | None = None,
        is_direct: bool = False,
    ) -> str:
        """Create a Matrix room and return its room_id."""
        response = await self._client.room_create(
            name=name,
            invite=invite or [],
            is_direct=is_direct,
        )
        if isinstance(response, RoomCreateResponse):
            self._rooms[name] = response.room_id
            self._trim_rooms_cache()
            return response.room_id
        raise RuntimeError(f"Failed to create room {name}: {response}")

    async def ensure_room(self, name: str, invite: list[str] | None = None) -> str:
        """Return existing room_id or create a new one."""
        if name in self._rooms:
            return self._rooms[name]
        return await self.create_room(name, invite=invite)

    def _trim_rooms_cache(self) -> None:
        if len(self._rooms) > _MAX_ROOMS_CACHE:
            # Evict oldest entries (simple FIFO)
            excess = len(self._rooms) - _MAX_ROOMS_CACHE
            for key in list(self._rooms.keys())[:excess]:
                del self._rooms[key]

    # -- messaging -----------------------------------------------------------

    async def send_text(self, room_id: str, body: str) -> str:
        """Send a text message to a room. Returns event_id."""
        response = await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": body},
        )
        return response.event_id

    async def sync(self, timeout_ms: int = 3000) -> list[dict[str, Any]]:
        """Pull new messages since last sync."""
        response = await self._client.sync(
            timeout=timeout_ms,
            since=self._since_token,
        )
        if not isinstance(response, SyncResponse):
            return []

        # Advance sync token so next call only gets new messages
        self._since_token = response.next_batch

        messages: list[dict[str, Any]] = []
        for room_id, room in response.rooms.join.items():
            for event in room.timeline.events:
                if isinstance(event, RoomMessageText):
                    messages.append(
                        {
                            "room_id": room_id,
                            "sender": event.sender,
                            "body": event.body,
                            "event_id": event.event_id,
                            "ts": event.server_timestamp / 1000.0,
                        }
                    )
        return messages

    # -- helpers -------------------------------------------------------------

    @property
    def user_id(self) -> str:
        return self._config.user_id


class MatrixTransport:
    """Pluggable transport layer backed by Matrix.

    When Matrix is not configured all methods silently no-op so the caller
    can unconditionally call them without branching.
    """

    def __init__(self, client: MatrixClient | None = None):
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def create_room(
        self,
        name: str,
        invite: list[str] | None = None,
    ) -> str:
        if self._client is None:
            return ""
        return await self._client.ensure_room(name, invite=invite)

    async def send(self, room_id: str, body: str) -> str:
        if self._client is None:
            return ""
        return await self._client.send_text(room_id, body)

    async def receive(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        return await self._client.sync()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
