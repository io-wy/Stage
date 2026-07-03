"""
Feishu-Matrix Bridge Bot

A lightweight bridge that exposes the HiClaw Manager over Feishu (Lark) DMs.
Each Feishu user gets a dedicated Matrix DM room with the bridge bot; the
bridge bot invites the Manager into that room. Messages are forwarded both
ways:

  Feishu user -> bridge bot -> Matrix room (with Manager) -> Manager
  Manager reply in Matrix room -> bridge bot -> Feishu user

This keeps the existing Matrix-centric architecture unchanged; workers do not
need to know about Feishu.

The Feishu side uses the official ``lark-channel-sdk`` (WebSocket transport),
so no public inbound webhook URL is required.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports: these packages are only required at runtime inside the worker
# environment. The file must still be importable for linting without them.
# ---------------------------------------------------------------------------
try:
    from lark_channel import FeishuChannel

    _FEISHU_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FEISHU_SDK_AVAILABLE = False
    FeishuChannel = None  # type: ignore[misc, assignment]

try:
    from nio import (
        AsyncClient,
        LoginResponse,
        RoomInviteResponse,
        RoomMemberEvent,
        RoomMessageText,
        ToDeviceError,
    )

    _MATRIX_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MATRIX_AVAILABLE = False
    AsyncClient = None  # type: ignore[misc, assignment]


@dataclass
class FeishuMatrixBridgeConfig:
    """Runtime configuration for the bridge."""

    # Feishu app credentials
    feishu_app_id: str
    feishu_app_secret: str
    feishu_domain: str = "https://open.feishu.cn"

    # Matrix bridge-bot credentials
    matrix_homeserver: str = ""
    matrix_user_id: str = ""
    matrix_access_token: str = ""
    matrix_password: str = ""
    matrix_device_name: str = "feishu-matrix-bridge"

    # Manager Matrix user that will be invited into every bridged room
    manager_matrix_user_id: str = ""

    # Where to persist the feishu_chat_id -> matrix_room_id mapping
    state_file: Path = Path("/tmp/feishu_matrix_bridge_state.json")

    @classmethod
    def from_env(cls) -> "FeishuMatrixBridgeConfig":
        return cls(
            feishu_app_id=os.environ.get("FEISHU_APP_ID", ""),
            feishu_app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            feishu_domain=os.environ.get("FEISHU_DOMAIN", "https://open.feishu.cn"),
            matrix_homeserver=os.environ.get("MATRIX_HOMESERVER", ""),
            matrix_user_id=os.environ.get("MATRIX_USER_ID", ""),
            matrix_access_token=os.environ.get("MATRIX_ACCESS_TOKEN", ""),
            matrix_password=os.environ.get("MATRIX_PASSWORD", ""),
            matrix_device_name=os.environ.get(
                "MATRIX_DEVICE_NAME", "feishu-matrix-bridge"
            ),
            manager_matrix_user_id=os.environ.get("MANAGER_MATRIX_USER_ID", ""),
            state_file=Path(
                os.environ.get(
                    "FEISHU_BRIDGE_STATE_FILE",
                    "/tmp/feishu_matrix_bridge_state.json",
                )
            ),
        )


class FeishuMatrixBridge:
    """Bidirectional bridge between Feishu private chats and Matrix DMs."""

    def __init__(self, config: FeishuMatrixBridgeConfig) -> None:
        if not _FEISHU_SDK_AVAILABLE:
            raise RuntimeError(
                "lark-channel-sdk is not installed; the bridge cannot run in this environment"
            )
        if not _MATRIX_AVAILABLE:
            raise RuntimeError(
                "matrix-nio is not installed; the bridge cannot run in this environment"
            )

        self._cfg = config
        self._feishu: Optional[FeishuChannel] = None
        self._matrix: Optional[AsyncClient] = None
        self._rooms: Dict[str, str] = {}  # feishu chat_id -> matrix room_id
        self._shutdown_event = asyncio.Event()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if not self._cfg.state_file.exists():
            return
        try:
            data = json.loads(self._cfg.state_file.read_text())
            self._rooms = data.get("rooms", {})
            logger.info(
                "Loaded %d bridged rooms from %s",
                len(self._rooms),
                self._cfg.state_file,
            )
        except Exception as exc:
            logger.warning("Failed to load bridge state: %s", exc)

    def _save_state(self) -> None:
        try:
            self._cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
            self._cfg.state_file.write_text(
                json.dumps({"rooms": self._rooms}, indent=2)
            )
        except Exception as exc:
            logger.warning("Failed to save bridge state: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._load_state()
        await self._start_matrix()
        await self._start_feishu()
        logger.info("Feishu-Matrix bridge started")
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        logger.info("Stopping Feishu-Matrix bridge...")
        self._shutdown_event.set()
        if self._feishu is not None:
            try:
                await self._feishu.disconnect()
            except Exception as exc:
                logger.warning("Feishu disconnect error: %s", exc)
        if self._matrix is not None:
            await self._matrix.close()

    # ------------------------------------------------------------------
    # Matrix side
    # ------------------------------------------------------------------

    async def _start_matrix(self) -> None:
        self._matrix = AsyncClient(
            self._cfg.matrix_homeserver,
            user=self._cfg.matrix_user_id,
        )

        if self._cfg.matrix_access_token:
            self._matrix.access_token = self._cfg.matrix_access_token
            whoami = await self._matrix.whoami()
            if hasattr(whoami, "user_id"):
                self._matrix.user_id = whoami.user_id
                logger.info("Matrix: logged in with access token as %s", whoami.user_id)
            else:
                raise RuntimeError(f"Matrix token login failed: {whoami}")
        elif self._cfg.matrix_password:
            resp = await self._matrix.login(
                self._cfg.matrix_user_id,
                self._cfg.matrix_password,
                device_name=self._cfg.matrix_device_name,
            )
            if isinstance(resp, LoginResponse):
                logger.info("Matrix: logged in with password as %s", resp.user_id)
            else:
                raise RuntimeError(f"Matrix password login failed: {resp}")
        else:
            raise RuntimeError("Matrix credentials not configured")

        self._matrix.add_event_callback(self._on_matrix_message, (RoomMessageText,))
        self._matrix.add_event_callback(self._on_matrix_member, (RoomMemberEvent,))
        self._matrix.add_event_callback(self._on_to_device, (ToDeviceError,))

        asyncio.create_task(self._matrix_sync_loop())

    async def _matrix_sync_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._matrix.sync(timeout=30000)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Matrix sync error: %s", exc)
                await asyncio.sleep(5)

    async def _get_or_create_room(self, feishu_chat_id: str) -> Optional[str]:
        if feishu_chat_id in self._rooms:
            return self._rooms[feishu_chat_id]

        manager = self._cfg.manager_matrix_user_id
        if not manager:
            logger.error("Manager Matrix user ID not configured")
            return None

        create_resp = await self._matrix.room_create(
            visibility="private",
            name=f"Feishu {feishu_chat_id}",
            invite=[manager],
        )
        if not hasattr(create_resp, "room_id"):
            logger.error("Failed to create Matrix room: %s", create_resp)
            return None

        room_id = create_resp.room_id
        logger.info(
            "Created Matrix room %s for Feishu chat %s",
            room_id,
            feishu_chat_id,
        )

        invite_resp = await self._matrix.room_invite(room_id, manager)
        if not isinstance(invite_resp, RoomInviteResponse):
            logger.warning("Invite response for manager: %s", invite_resp)

        self._rooms[feishu_chat_id] = room_id
        self._save_state()
        return room_id

    async def _on_matrix_message(self, room: Any, event: RoomMessageText) -> None:
        if not self._matrix:
            return

        sender = getattr(event, "sender", "")
        if sender == self._matrix.user_id:
            return  # ignore echo from the bridge bot itself

        room_id = getattr(room, "room_id", "")
        feishu_chat_id = self._find_feishu_chat_by_room(room_id)
        if not feishu_chat_id:
            return  # message in a room we are not bridging

        # Only forward messages from the configured manager.
        if sender != self._cfg.manager_matrix_user_id:
            logger.debug(
                "Ignoring message from non-manager %s in bridged room", sender
            )
            return

        text = getattr(event, "body", "") or ""
        await self._send_feishu_text(feishu_chat_id, text)

    async def _on_matrix_member(self, room: Any, event: Any) -> None:
        if (
            getattr(event, "membership", "") == "invite"
            and getattr(event, "state_key", "") == self._matrix.user_id
        ):
            room_id = getattr(room, "room_id", "") or getattr(event, "room_id", "")
            if room_id:
                await self._matrix.join(room_id)
                logger.info("Auto-joined Matrix room %s", room_id)

    async def _on_to_device(self, event: Any) -> None:
        pass

    def _find_feishu_chat_by_room(self, room_id: str) -> Optional[str]:
        for chat_id, bridged_room in self._rooms.items():
            if bridged_room == room_id:
                return chat_id
        return None

    async def _send_matrix_text(self, room_id: str, text: str) -> None:
        content = {"msgtype": "m.text", "body": text}
        await self._matrix.room_send(room_id, "m.room.message", content)

    # ------------------------------------------------------------------
    # Feishu side
    # ------------------------------------------------------------------

    async def _start_feishu(self) -> None:
        self._feishu = FeishuChannel(
            app_id=self._cfg.feishu_app_id,
            app_secret=self._cfg.feishu_app_secret,
            domain=self._cfg.feishu_domain,
        )
        self._feishu.on("message", self._on_feishu_message)
        self._feishu.on("error", self._on_feishu_error)
        asyncio.create_task(self._feishu.connect())
        logger.info("Feishu channel connecting via WebSocket")

    async def _on_feishu_message(self, msg: Any) -> None:
        chat_id = getattr(msg, "chat_id", None)
        if not chat_id:
            logger.warning("Feishu message without chat_id: %s", msg)
            return

        # Only handle p2p (single) chats. Group chats are ignored in this
        # minimal bridge.
        chat_type = getattr(msg, "chat_type", "p2p")
        if chat_type != "p2p":
            logger.debug("Ignoring non-p2p Feishu message (chat_type=%s)", chat_type)
            return

        text = getattr(msg, "content_text", "") or ""
        room_id = await self._get_or_create_room(chat_id)
        if not room_id:
            logger.error("No Matrix room available for Feishu chat %s", chat_id)
            return

        prefix = f"[Feishu {chat_id}]\n"
        await self._send_matrix_text(room_id, prefix + text)

    async def _on_feishu_error(self, err: Any) -> None:
        logger.error("Feishu channel error: %s", err)

    async def _send_feishu_text(self, chat_id: str, text: str) -> None:
        if self._feishu is None:
            return
        try:
            await self._feishu.send(chat_id, {"text": text})
            logger.debug("Sent Feishu message to chat %s", chat_id)
        except Exception as exc:
            logger.exception("Failed to send Feishu message to %s: %s", chat_id, exc)


async def main_async() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = FeishuMatrixBridgeConfig.from_env()
    bridge = FeishuMatrixBridge(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bridge.stop()))

    await bridge.start()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
