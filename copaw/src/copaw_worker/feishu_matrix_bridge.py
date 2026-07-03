"""
Feishu-Matrix Bridge Bot (admin-proxy mode)

This bridge lets a Feishu user talk to the HiClaw Manager through Matrix by
proxying via the admin's Matrix account.

  Feishu user -> Feishu bot -> bridge bot -> admin's Matrix account
                                              -> Manager (in admin-Manager DM)
  Manager reply in admin-Manager DM -> bridge bot -> Feishu user

The bridge logs in as the admin Matrix user, finds (or creates) the direct
chat room between admin and Manager, and forwards messages both ways.
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
        JoinedMembersResponse,
        JoinedRoomsResponse,
        LoginResponse,
        RoomCreateResponse,
        RoomInviteResponse,
        RoomMemberEvent,
        RoomMessageText,
        RoomVisibility,
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
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_domain: str = "https://open.feishu.cn"

    # Admin Matrix credentials (the bridge proxies as this user)
    matrix_homeserver: str = ""
    matrix_user_id: str = ""
    matrix_access_token: str = ""
    matrix_password: str = ""
    matrix_device_name: str = "feishu-matrix-bridge"

    # Manager Matrix user that admin talks to
    manager_matrix_user_id: str = ""

    # Where to persist the feishu_chat_id for the current conversation
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
    """Bidirectional bridge: Feishu user <-> admin-Manager Matrix DM."""

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
        self._dm_room_id: Optional[str] = None
        self._current_feishu_chat_id: Optional[str] = None
        self._shutdown_event = asyncio.Event()
        # Decouple lark-channel-sdk callbacks from matrix-nio I/O.
        self._inbound_queue: asyncio.Queue[Any] = asyncio.Queue()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if not self._cfg.state_file.exists():
            return
        try:
            data = json.loads(self._cfg.state_file.read_text())
            self._current_feishu_chat_id = data.get("feishu_chat_id")
            self._dm_room_id = data.get("dm_room_id")
            logger.info(
                "Loaded bridge state: feishu_chat_id=%s dm_room_id=%s",
                self._current_feishu_chat_id,
                self._dm_room_id,
            )
        except Exception as exc:
            logger.warning("Failed to load bridge state: %s", exc)

    def _save_state(self) -> None:
        try:
            self._cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
            self._cfg.state_file.write_text(
                json.dumps(
                    {
                        "feishu_chat_id": self._current_feishu_chat_id,
                        "dm_room_id": self._dm_room_id,
                    },
                    indent=2,
                )
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
        asyncio.create_task(self._process_inbound_loop())
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
    # Matrix side: log in as admin, find/create admin-Manager DM
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

        self._dm_room_id = await self._find_or_create_dm_room()
        if not self._dm_room_id:
            raise RuntimeError("Could not find or create admin-Manager DM room")

        self._matrix.add_event_callback(self._on_matrix_message, (RoomMessageText,))
        self._matrix.add_event_callback(self._on_matrix_member, (RoomMemberEvent,))
        self._matrix.add_event_callback(self._on_to_device, (ToDeviceError,))

        asyncio.create_task(self._matrix_sync_loop())

    async def _find_or_create_dm_room(self) -> Optional[str]:
        manager = self._cfg.manager_matrix_user_id
        if not manager:
            logger.error("Manager Matrix user ID not configured")
            return None

        # If state has a DM room, try to use it.
        if self._dm_room_id:
            logger.info("Using DM room from state: %s", self._dm_room_id)
            return self._dm_room_id

        # Search joined rooms for an existing admin-Manager DM.
        joined_resp = await self._matrix.joined_rooms()
        if isinstance(joined_resp, JoinedRoomsResponse):
            for room_id in joined_resp.rooms:
                members_resp = await self._matrix.joined_members(room_id)
                if isinstance(members_resp, JoinedMembersResponse):
                    members = {m.user_id for m in members_resp.members}
                    if members == {self._matrix.user_id, manager}:
                        logger.info("Found existing admin-Manager DM: %s", room_id)
                        return room_id

        # Create a new direct chat room and invite the manager.
        create_resp = await self._matrix.room_create(
            visibility=RoomVisibility.private,
            invite=[manager],
            is_direct=True,
        )
        if isinstance(create_resp, RoomCreateResponse):
            room_id = create_resp.room_id
            logger.info("Created admin-Manager DM room: %s", room_id)
            return room_id

        logger.error("Failed to create admin-Manager DM room: %s", create_resp)
        return None

    async def _matrix_sync_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._matrix.sync(timeout=30000)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Matrix sync error: %s", exc)
                await asyncio.sleep(5)

    async def _on_matrix_message(self, room: Any, event: RoomMessageText) -> None:
        if not self._matrix:
            return

        sender = getattr(event, "sender", "")
        if sender == self._matrix.user_id:
            return  # ignore echo from admin account

        room_id = getattr(room, "room_id", "")
        if room_id != self._dm_room_id:
            return  # ignore messages from other rooms

        # Only forward messages from the manager.
        if sender != self._cfg.manager_matrix_user_id:
            logger.debug("Ignoring message from non-manager %s in DM room", sender)
            return

        if not self._current_feishu_chat_id:
            logger.warning("Received Manager reply but no Feishu chat_id is known")
            return

        text = getattr(event, "body", "") or ""
        await self._send_feishu_text(self._current_feishu_chat_id, text)

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

    async def _send_matrix_text(self, room_id: str, text: str) -> None:
        manager = self._cfg.manager_matrix_user_id
        if manager:
            # Mention the manager so channel-mode routing treats the message
            # as addressed to them and starts an agent run.
            body = f"{manager} {text}"
            content: Dict[str, Any] = {
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": (
                    f'<a href="https://matrix.to/#/{manager}">{manager}</a> '
                    f"{text}"
                ),
                "m.mentions": {"user_ids": [manager]},
            }
        else:
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
        self._feishu.on("message", self._on_feishu_message_sync)
        self._feishu.on("error", self._on_feishu_error_sync)
        asyncio.create_task(self._feishu.connect())
        logger.info("Feishu channel connecting via WebSocket")

    def _on_feishu_message_sync(self, msg: Any) -> None:
        try:
            self._inbound_queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("Inbound Feishu message queue is full; dropping message")

    def _on_feishu_error_sync(self, err: Any) -> None:
        asyncio.create_task(self._on_feishu_error(err))

    async def _process_inbound_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                msg = await asyncio.wait_for(
                    self._inbound_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            try:
                await self._handle_feishu_message(msg)
            except Exception as exc:
                logger.exception("Failed to handle Feishu message: %s", exc)
            finally:
                self._inbound_queue.task_done()

    async def _handle_feishu_message(self, msg: Any) -> None:
        chat_id = getattr(msg, "chat_id", None)
        if not chat_id:
            logger.warning("Feishu message without chat_id: %s", msg)
            return

        # Only handle p2p (single) chats.
        chat_type = getattr(msg, "chat_type", "p2p")
        if chat_type != "p2p":
            logger.debug("Ignoring non-p2p Feishu message (chat_type=%s)", chat_type)
            return

        self._current_feishu_chat_id = chat_id
        self._save_state()

        text = getattr(msg, "content_text", "") or ""
        if not self._dm_room_id:
            logger.error("No admin-Manager DM room available")
            return

        await self._send_matrix_text(self._dm_room_id, text)
        logger.info("Forwarded Feishu message to admin-Manager DM")

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
