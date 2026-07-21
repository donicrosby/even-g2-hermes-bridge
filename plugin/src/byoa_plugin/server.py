"""WebSocket server for the even-g2 plugin.

Accepts connections from the glasses-app, validates the hello handshake,
parses inbound frames, and exposes `send_frame()` for pushing outbound
frames to a specific chat_id.

Also serves HTTP endpoints alongside WS on the same port:
  - GET /health  → JSON status
  - GET /qr      → PNG image of the QR code for glasses-app bootstrap

The server binds to BridgeConfig.ws_host:ws_port (default 127.0.0.1:8767).
External exposure is via Tailscale Serve or a user-provided reverse proxy
(see net.py and setup_flow.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import anyio
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from byoa_plugin import protocol as proto
from byoa_plugin.http_endpoints import HttpEndpointHandler

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry

LOG = logging.getLogger("byoa_plugin.server")

# Cap accumulated PCM bytes per turn (~8 MiB ≈ 4 minutes of 16kHz mono s16).
MAX_PCM_BYTES = 8 * 1024 * 1024

# WS keepalive interval — defeats idle proxies (e.g. nginx default 60s).
PING_INTERVAL_SEC = 30

# Frame handlers receive (chat_id, parsed_frame_or_None, raw_bytes_or_None).
# `parsed` is set for text frames, `raw` is set for binary frames.
TextHandler = Callable[[str, dict], None]
BinaryHandler = Callable[[str, bytes], None]


class BridgeServer:
    """WebSocket server that bridges glasses-app ↔ plugin."""

    def __init__(  # noqa: PLR0913
        self,
        cfg: BridgeConfig,
        registry: ConnectionRegistry,
        *,
        on_text: Callable[[str, str], None] | None = None,
        on_audio_stop: Callable[[str, bytes], None] | None = None,
        on_sessions_list: Callable[[str], None] | None = None,
        on_sessions_switch: Callable[[str, str], None] | None = None,
        on_sessions_new: Callable[[str], None] | None = None,
        on_stop: Callable[[str], None] | None = None,
        active_session_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        """Initialize the bridge server and its event callbacks."""
        self.cfg = cfg
        self.registry = registry
        self._http_handler = HttpEndpointHandler(cfg, registry)
        self._on_text = on_text
        self._on_audio_stop = on_audio_stop
        self._on_sessions_list = on_sessions_list
        self._on_sessions_switch = on_sessions_switch
        self._on_sessions_new = on_sessions_new
        self._on_stop = on_stop
        self._active_session_lookup = active_session_lookup
        self._server = None

    async def start(self) -> None:
        """Start the WebSocket server and HTTP endpoint handler."""
        host = self.cfg.ws_host
        port = self.cfg.ws_port
        LOG.info("WS server starting on %s:%s", host, port)
        self._server = await serve(
            self._handle,
            host,
            port,
            # Allow the glasses' token-only auth; no subprotocol required.
            subprotocols=None,
            # Defaults are fine: max_size 1 MiB is too small for PCM bursts,
            # so bump to MAX_PCM_BYTES + headroom.
            max_size=MAX_PCM_BYTES + 65536,
            ping_interval=None,  # we send our own text-level keepalives
            ping_timeout=None,
            # Tight close handshake timeout so shutdown isn't held up by
            # clients that disappear without ACKing the close frame.
            close_timeout=2.0,
            # Multiplex HTTP endpoints alongside WS on the same port.
            process_request=self._http_handler,
        )
        LOG.info("WS server listening on %s:%s", host, port)

    async def stop(self) -> None:
        """Stop the server and wait for active connections to close."""
        # Close the server — connections drop, handlers exit, and ping
        # tasks are cancelled automatically by their TaskGroup scopes.
        # wait_closed() bounded by 5s so a stuck handler can't hang shutdown.
        if self._server is not None:
            self._server.close()
            try:
                with anyio.fail_after(5.0):
                    await self._server.wait_closed()
            except TimeoutError:
                LOG.warning("server.stop() timed out; forcing shutdown")
            self._server = None
        LOG.info("WS server stopped")

    async def _handle(self, ws: ServerConnection) -> None:  # noqa: C901, PLR0912, PLR0915
        """Handle one WebSocket connection from hello handshake to teardown."""
        chat_id: str | None = None
        ping_task: asyncio.Task[None] | None = None

        try:
            # ----- Phase 1: hello handshake -----
            try:
                first = await ws.recv()
            except ConnectionClosed:
                return
            if isinstance(first, bytes):
                await ws.close(code=1002, reason="expected hello text frame")
                return
            try:
                hello = proto.parse_client(first)
            except ValueError:
                await ws.close(code=1002, reason="malformed hello")
                return
            if hello.get("t") != "hello":
                await ws.close(code=1002, reason="expected hello first")
                return

            client_token = hello.get("token", "")
            expected = self.cfg.token
            if not isinstance(client_token, str) or not hmac.compare_digest(
                client_token, expected,
            ):
                LOG.warning("auth rejected: bad token")
                await ws.close(code=1008, reason="unauthorized")
                return

            chat_id = str(hello.get("device") or "g2")
            await self.registry.register(chat_id, ws)
            LOG.info("hello ok: chat_id=%s", chat_id)

            active = (
                self._active_session_lookup(chat_id)
                if self._active_session_lookup
                else None
            )
            caps = ["text", "voice", "tool-events", "sessions", "streaming"]
            await ws.send(proto.hello_ok(active=active, caps=caps))

            # ----- Phase 2: frame dispatch loop -----
            audio_buf = bytearray()
            capturing = False
            ping_task = asyncio.create_task(self._ping_loop(ws, chat_id))
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    if capturing:
                        if len(audio_buf) + len(raw) > MAX_PCM_BYTES:
                            LOG.warning(
                                "PCM cap hit for chat_id=%s; truncating",
                                chat_id,
                            )
                            audio_buf.extend(bytes(raw)[: MAX_PCM_BYTES - len(audio_buf)])
                            capturing = False
                        else:
                            audio_buf.extend(raw)
                    else:
                        LOG.debug(
                            "ignoring binary frame outside audio capture (chat_id=%s)",
                            chat_id,
                        )
                    continue

                try:
                    frame = proto.parse_client(raw)
                except ValueError as e:
                    LOG.warning("malformed frame from chat_id=%s: %s", chat_id, e)
                    continue

                match frame.get("t"):
                    case "text":
                        text_content = str(frame.get("text", ""))
                        if self._on_text:
                            self._on_text(chat_id, text_content)
                    case "audio.start":
                        audio_buf.clear()
                        capturing = True
                        LOG.debug("audio capture started chat_id=%s", chat_id)
                    case "audio.stop":
                        capturing = False
                        pcm = bytes(audio_buf)
                        audio_buf.clear()
                        LOG.debug(
                            "audio capture stopped chat_id=%s bytes=%d",
                            chat_id,
                            len(pcm),
                        )
                        if self._on_audio_stop:
                            self._on_audio_stop(chat_id, pcm)
                    case "sessions.list":
                        if self._on_sessions_list:
                            self._on_sessions_list(chat_id)
                    case "sessions.switch":
                        target = str(frame.get("id", "+1"))
                        if self._on_sessions_switch:
                            self._on_sessions_switch(chat_id, target)
                    case "sessions.new":
                        if self._on_sessions_new:
                            self._on_sessions_new(chat_id)
                    case "stop":
                        if self._on_stop:
                            self._on_stop(chat_id)
                    case unknown_t:
                        LOG.warning(
                            "unknown frame type %r from chat_id=%s", unknown_t, chat_id,
                        )
        except ConnectionClosed:
            pass
        except Exception:
            LOG.exception("handler crashed for chat_id=%s", chat_id)
        finally:
            if ping_task is not None:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task
            if chat_id is not None:
                await self.registry.unregister(chat_id, ws)
            LOG.debug("handler exited chat_id=%s", chat_id)

    async def _ping_loop(self, ws: ServerConnection, chat_id: str) -> None:
        """Send periodic text-level keepalives while the socket is open."""
        try:
            while True:
                await anyio.sleep(PING_INTERVAL_SEC)
                await ws.ping()
                LOG.debug("sent ping chat_id=%s", chat_id)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except OSError as e:
            LOG.debug("ping loop ended chat_id=%s: %s", chat_id, e)
