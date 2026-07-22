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

from byoa_plugin import wire as proto
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
        """Stub inbound message event — mirrors the real gateway dataclass."""

        def __init__(
            self,
            text: str = "",
            message_type: str = "text",
            source: object = None,
            message_id: str | None = None,
            **_kwargs: object,
        ) -> None:
            """Store inbound message fields."""
            self.text = text
            self.message_type = message_type
            self.source = source
            self.message_id = message_id

    class BasePlatformAdapter:  # type: ignore[no-redef]
        """Stub base class matching the real gateway interface."""

        def __init__(self, config: object, platform: object) -> None:
            """Store config and platform identity."""
            self.config = config
            self.platform = platform
            self._message_handler: Callable[..., Any] | None = None

        def build_source(self, chat_id: str, **kwargs: object) -> object:
            """Stub — returns a SimpleNamespace mimicking SessionSource."""
            from types import SimpleNamespace

            return SimpleNamespace(
                platform=getattr(self, "platform", None),
                chat_id=chat_id,
                **kwargs,
            )

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

        # Pending BYOA futures: chat_id → Future that resolves with the full
        # assistant response when edit_message(finalize=True) fires.
        self._byoa_futures: dict[str, asyncio.Future[str]] = {}

        # v1 limitation: plugin session hooks don't receive chat_id in their
        # payload (verified upstream contract), so we attribute session events
        # to whatever chat_id most recently sent an inbound frame. Single-pair
        # per adapter is the documented v1 scope.
        self._last_chat_id: str | None = None

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
        from byoa_plugin.hooks import set_adapter

        set_adapter(self)
        self._server = BridgeServer(
            self.cfg,
            self.registry,
            on_text=self._on_text,
            on_audio_stop=self._on_audio_stop,
            on_sessions_list=self._on_sessions_list,
            on_sessions_switch=self._on_sessions_switch,
            on_sessions_new=self._on_sessions_new,
            on_stop=self._on_stop,
            active_session_lookup=self.session_for_chat,
        )
        self._server.set_adapter(self)
        await self._server.start()
        self._mark_connected()

        LOG.info(
            "connect state: _message_handler=%s gateway_runner=%s",
            self._message_handler is not None,
            getattr(self, "gateway_runner", None) is not None,
        )
        if self.gateway_runner and not self._message_handler:
            LOG.info("wiring _message_handler from gateway_runner._handle_message")
            self.set_message_handler(self.gateway_runner._handle_message)
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

        def _on_done(t: asyncio.Task[None]) -> None:
            self._bg_tasks.discard(t)
            if t.exception() and not t.cancelled():
                LOG.exception("spawned task failed", error=str(t.exception()))

        task.add_done_callback(_on_done)

    def _on_text(self, chat_id: str, text: str) -> None:
        self._last_chat_id = chat_id
        LOG.info("inbound text chat_id=%s len=%d", chat_id, len(text))
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=self.build_source(chat_id=chat_id, user_id=self.platform.value or "g2"),
        )
        self._spawn(self.handle_message(event))

    def _on_audio_stop(self, chat_id: str, pcm: bytes) -> None:
        self._last_chat_id = chat_id
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

    def _on_sessions_list(self, chat_id: str) -> None:
        """Respond with local session list AND forward /sessions to gateway."""
        self._last_chat_id = chat_id
        # Emit sessions frame from local state so the glasses have SOMETHING
        # to render even before the gateway replies.
        self._spawn(self._emit_sessions_frame(chat_id))
        # Forward the slash command for conversation-context sync.
        event = MessageEvent(
            text="/sessions",
            message_type=MessageType.TEXT,
            source=self.build_source(chat_id=chat_id, user_id=self.platform.value or "g2"),
        )
        self._spawn(self.handle_message(event))

    async def _emit_sessions_frame(self, chat_id: str) -> None:
        """Send a sessions frame derived from _session_by_chat."""
        session_id = self._session_by_chat.get(chat_id)
        if session_id:
            items = [{"id": session_id, "name": session_id[:16]}]
            frame = proto.sessions(items, active=session_id)
        else:
            items = [{"id": "_none", "name": "No Sessions Found"}]
            frame = proto.sessions(items, active="_none")
        await self.registry.send_frame(chat_id, frame)
        LOG.info(
            "frame direction=out frame_type=sessions byte_size=%d chat_id=%s",
            len(frame), chat_id,
        )

    def _on_sessions_switch(self, chat_id: str, target: str) -> None:
        """Handle session switch. Relative offsets (+1/-1) are resolved locally;
        absolute session IDs are forwarded to the gateway.
        """
        self._last_chat_id = chat_id
        if target.startswith(("+", "-")):
            self._spawn(self._emit_sessions_frame(chat_id))
            return
        event = MessageEvent(
            text=f"/resume {target}",
            message_type=MessageType.TEXT,
            source=self.build_source(chat_id=chat_id, user_id=self.platform.value or "g2"),
        )
        self._spawn(self.handle_message(event))

    def _on_sessions_new(self, chat_id: str) -> None:
        """Forward /new command to the gateway."""
        self._last_chat_id = chat_id
        event = MessageEvent(
            text="/new",
            message_type=MessageType.TEXT,
            source=self.build_source(chat_id=chat_id, user_id=self.platform.value or "g2"),
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
        LOG.info("send chat_id=%s content_len=%d", chat_id, len(content or ""))
        state = self.registry.stream_state(chat_id)
        state.reset()
        delta = state.delta_for(content)
        if delta:
            ok = await self.registry.send_frame(chat_id, proto.assistant_delta(delta))
            if not ok:
                if chat_id in self._byoa_futures:
                    LOG.info("send ws_push_skipped chat_id=%s (byoa, no ws)", chat_id)
                else:
                    LOG.warning("send failed chat_id=%s (no active connection)", chat_id)
                    return SendResult(success=False, error="no active connection")
            else:
                LOG.info(
                    "frame direction=out frame_type=assistant.delta byte_size=%d chat_id=%s",
                    len(delta), chat_id,
                )
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
        send_ok = True
        if delta:
            send_ok = await self.registry.send_frame(chat_id, proto.assistant_delta(delta))
        if finalize:
            await self.registry.send_frame(chat_id, proto.turn_done())
            fut = self._byoa_futures.pop(chat_id, None)
            if fut and not fut.done():
                fut.set_result(content)
        if not send_ok:
            return SendResult(success=False, error="no active connection")
        return SendResult(success=True, message_id=message_id)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """Return minimal chat metadata for the gateway."""
        return {"name": chat_id, "type": "dm"}

    def register_byoa_future(self, chat_id: str) -> asyncio.Future[str]:
        """Register a Future that resolves when the BYOA turn for chat_id finalizes.

        The BYOA HTTPS handler calls this before dispatching handle_message,
        then awaits the future to block until the gateway has finished
        streaming the response. edit_message(finalize=True) resolves it.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._byoa_futures[chat_id] = fut
        return fut

    def cancel_byoa_future(self, chat_id: str) -> None:
        """Cancel and remove a pending BYOA future (e.g., on timeout)."""
        fut = self._byoa_futures.pop(chat_id, None)
        if fut and not fut.done():
            fut.cancel()

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
