"""Hermes platform adapter for Even Realities G2.

Bridges the glasses-app (custom Even Hub SDK app on the phone) to the
Hermes Gateway via a persistent WebSocket. Inherits the standard
BasePlatformAdapter interface — the same one Telegram/Discord/Signal use —
so the gateway's streaming, sessions, tools, and pairing all flow through
the same code path as every other chat platform.

When loaded inside the Hermes Gateway process, `gateway.platforms.base`
is importable directly. For development in isolation (running tests,
inspecting the module), we fall back to local stub types that match the
production interface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import anyio

from byoa_plugin import protocol as proto
from byoa_plugin.connections import ConnectionRegistry
from byoa_plugin.server import BridgeServer

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

# ---- Hermes Gateway type shim ---------------------------------------------
# When this module is loaded by the Hermes Gateway process, `gateway` is
# available. For dev/test in isolation we define minimal stubs that match
# the interface but don't actually run the gateway.

try:
    from gateway.platforms.base import (  # type: ignore[import-not-found]
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
    )
except ImportError:  # pragma: no cover — only hit outside Hermes runtime
    LOG = logging.getLogger("byoa_plugin.adapter")

    class SendResult:  # type: ignore[no-redef]
        """Stub return type for adapter.send/edit_message."""

        def __init__(self, success: bool, *, message_id: str = "", error: str = "") -> None:  # noqa: FBT001
            """Store send result fields."""
            self.success = success
            self.message_id = message_id
            self.error = error

    class MessageType:  # type: ignore[no-redef]
        """Stub message-type enum."""

        TEXT = "text"
        VOICE = "voice"

    class MessageEvent:  # type: ignore[no-redef]
        """Stub inbound message event."""

        def __init__(
            self,
            chat_id: str,
            text: str = "",
            message_type: str = "text",
            metadata: dict[str, Any] | None = None,
        ) -> None:
            """Store inbound message fields."""
            self.chat_id = chat_id
            self.text = text
            self.message_type = message_type
            self.metadata = metadata or {}

    class BasePlatformAdapter:  # type: ignore[no-redef]
        """Stub base class matching the real gateway interface."""

        def __init__(self, config: object, platform: object) -> None:
            """Store config and platform identity."""
            self.config = config
            self.platform = platform
            self._message_handler: Callable[..., Any] | None = None

        def _mark_connected(self) -> None:
            """Stub — no-op outside the gateway."""

        def _mark_disconnected(self) -> None:
            """Stub — no-op outside the gateway."""

        def set_message_handler(self, handler: Callable[..., Any]) -> None:
            """Store the gateway's message-handling callback."""
            self._message_handler = handler

        async def handle_message(self, event: MessageEvent) -> None:
            """Forward an inbound event to the gateway's handler."""
            if self._message_handler is not None:
                await self._message_handler(event)

    LOG.info("using stub BasePlatformAdapter (gateway not installed)")

# ---- Platform constant ----------------------------------------------------

try:
    from gateway.config import Platform  # type: ignore[import-not-found]
    EVEN_G2 = Platform("even-g2")
except ImportError:
    class Platform:  # type: ignore[no-redef]
        """Stub platform enum value."""

        def __init__(self, name: str) -> None:
            """Store the platform name as the enum value."""
            self.value = name

        def __eq__(self, other: object) -> bool:  # type: ignore[override]
            """Compare by value."""
            return isinstance(other, Platform) and self.value == other.value

        def __hash__(self) -> int:  # type: ignore[override]
            """Hash by value."""
            return hash(self.value)

    EVEN_G2 = Platform("even-g2")

LOG = logging.getLogger("byoa_plugin.adapter")


class EvenG2Adapter(BasePlatformAdapter):
    """Hermes platform adapter that hosts a WS server for the glasses-app.

    Inbound: glasses-app → WS server → adapter.handle_message() → gateway
    Outbound: gateway → adapter.send/edit_message() → WS push → glasses-app
    """

    def __init__(self, config: object) -> None:
        """Initialize the adapter from PlatformConfig + env vars."""
        super().__init__(config, EVEN_G2)

        extra = getattr(config, "extra", {}) or {}
        # Merge env-driven config with PlatformConfig.extra (env takes priority
        # per Hermes's env_enablement pattern).
        from byoa_plugin.config import BridgeConfig

        self.cfg = BridgeConfig.from_env()
        # Apply any extra overrides the gateway supplied (dashboard config, etc).
        if extra.get("token"):
            self.cfg.token = extra["token"]
        if extra.get("host"):
            self.cfg.ws_host = extra["host"]
        if extra.get("port"):
            self.cfg.ws_port = int(extra["port"])
        if extra.get("public_url"):
            self.cfg.public_url = extra["public_url"]

        self.registry = ConnectionRegistry()

        # chat_id → session_id mapping (Hermes owns sessions; we just route).
        self._session_by_chat: dict[str, str] = {}

        # Fire-and-forget task references — prevents GC before completion.
        # Primitives migrated to anyio (Event, Lock, sleep); bg-task tracking
        # still uses asyncio.create_task — full TaskGroup migration requires
        # reworking the adapter lifecycle (long-lived tg owned by connect()).
        self._bg_tasks: set[asyncio.Task] = set()

        # Pending transcript results from ASR (audio.stop → transcribe → handle).
        self._asr_transcribe: Callable[[bytes], str] | None = None

        self._server: BridgeServer | None = None
        self._stop_event = anyio.Event()

    # ---- Lifecycle ---------------------------------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:  # noqa: ARG002
        """Start the WS server and mark the platform as connected."""
        self._server = BridgeServer(
            self.cfg,
            self.registry,
            on_text=self._on_text,
            on_audio_stop=self._on_audio_stop,
            on_sessions_list=self._on_sessions_list,
            on_sessions_switch=self._on_sessions_switch,
            on_sessions_new=self._on_sessions_new,
            on_stop=self._on_stop,
        )
        await self._server.start()
        self._mark_connected()
        advertised = self.cfg.advertised_url
        LOG.info(
            "even-g2 connected: bind=%s:%s advertised=%s",
            self.cfg.ws_host,
            self.cfg.ws_port,
            advertised,
        )
        return True

    async def disconnect(self) -> None:
        """Stop the WS server and mark the platform as disconnected."""
        if self._server is not None:
            await self._server.stop()
            self._server = None
        self._mark_disconnected()
        LOG.info("even-g2 disconnected")

    # ---- Inbound handlers (called by BridgeServer) ------------------------

    def _spawn(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _on_text(self, chat_id: str, text: str) -> None:
        LOG.info("inbound text chat_id=%s len=%d", chat_id, len(text))
        event = MessageEvent(
            chat_id=chat_id,
            text=text,
            message_type=MessageType.TEXT,
            metadata={"platform": "even-g2", "device": chat_id},
        )
        self._spawn(self.handle_message(event))

    def _on_audio_stop(self, chat_id: str, pcm: bytes) -> None:
        self._spawn(self._handle_voice(chat_id, pcm))

    async def _handle_voice(self, chat_id: str, pcm: bytes) -> None:
        try:
            from byoa_plugin.asr import transcribe
            text = transcribe(pcm, self.cfg)
        except (OSError, RuntimeError):
            LOG.exception("ASR failed chat_id=%s", chat_id)
            await self.registry.send_frame(chat_id, proto.error("voice transcription failed"))
            return

        if not text.strip():
            await self.registry.send_frame(
                chat_id, proto.transcript(""),
            )
            await self.registry.send_frame(
                chat_id, proto.error("didn't catch that"),
            )
            return

        await self.registry.send_frame(chat_id, proto.transcript(text))
        LOG.info("inbound voice chat_id=%s transcript=%r", chat_id, text[:80])
        event = MessageEvent(
            chat_id=chat_id,
            text=text,
            message_type=MessageType.VOICE,
            metadata={"platform": "even-g2", "device": chat_id, "asr": True},
        )
        await self.handle_message(event)

    def _on_sessions_list(self, chat_id: str) -> None:
        """Forward /sessions command to the gateway."""
        event = MessageEvent(
            chat_id=chat_id,
            text="/sessions",
            message_type=MessageType.TEXT,
            metadata={"platform": "even-g2", "device": chat_id},
        )
        self._spawn(self.handle_message(event))

    def _on_sessions_switch(self, chat_id: str, target: str) -> None:
        """Forward /resume command to the gateway."""
        event = MessageEvent(
            chat_id=chat_id,
            text=f"/resume {target}",
            message_type=MessageType.TEXT,
            metadata={"platform": "even-g2", "device": chat_id},
        )
        self._spawn(self.handle_message(event))

    def _on_sessions_new(self, chat_id: str) -> None:
        """Forward /new command to the gateway."""
        event = MessageEvent(
            chat_id=chat_id,
            text="/new",
            message_type=MessageType.TEXT,
            metadata={"platform": "even-g2", "device": chat_id},
        )
        self._spawn(self.handle_message(event))

    def _on_stop(self, chat_id: str) -> None:
        LOG.info("stop chat_id=%s (interrupt requested)", chat_id)
        # v1: we log the stop. The gateway's own interrupt path handles it
        # via the standard cancellation API; we may need to call into that.

    # ---- Outbound delivery (called by Hermes Gateway) ---------------------

    async def send(
        self, chat_id: str, content: str, reply_to: object = None, metadata: object = None,  # noqa: ARG002
    ) -> SendResult:
        """Deliver an assistant message — full text pushed as a delta."""
        state = self.registry.stream_state(chat_id)
        state.reset()
        delta = state.delta_for(content)
        if delta:
            ok = await self.registry.send_frame(chat_id, proto.assistant_delta(delta))
            if not ok:
                return SendResult(success=False, error="no active connection")
        return SendResult(success=True, message_id="g2")

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
        metadata: object = None,  # noqa: ARG002
    ) -> SendResult:
        """Send a streaming delta update for an existing assistant message."""
        state = self.registry.stream_state(chat_id)
        delta = state.delta_for(content)
        if delta:
            ok = await self.registry.send_frame(chat_id, proto.assistant_delta(delta))
            if not ok:
                return SendResult(success=False, error="no active connection")
        if finalize:
            await self.registry.send_frame(chat_id, proto.turn_done())
        return SendResult(success=True, message_id=message_id)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """Return minimal chat metadata for the gateway."""
        return {"name": chat_id, "type": "dm"}

    # ---- Session tracking --------------------------------------------------

    def bind_session(self, chat_id: str, session_id: str) -> None:
        """Record which gateway session a chat_id maps to."""
        self._session_by_chat[chat_id] = session_id

    def session_for_chat(self, chat_id: str) -> str | None:
        """Return the session_id bound to a chat_id, if any."""
        return self._session_by_chat.get(chat_id)

    def chat_for_session(self, session_id: str) -> str | None:
        """Return the chat_id bound to a session_id, if any."""
        for cid, sid in self._session_by_chat.items():
            if sid == session_id:
                return cid
        return None
