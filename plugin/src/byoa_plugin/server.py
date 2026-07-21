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
from collections.abc import Callable
from typing import TYPE_CHECKING

import anyio
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from byoa_plugin import protocol as proto
from byoa_plugin.http_endpoints import HttpEndpointHandler
from byoa_plugin.log import get_logger

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry

LOG = get_logger("byoa_plugin.server")

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

        async def send_frame(frame_str: str, frame_type: str) -> None:
            """Send + log an outbound text frame to the current connection."""
            await ws.send(frame_str)
            LOG.info(
                "frame",
                direction="out",
                frame_type=frame_type,
                byte_size=len(frame_str),
                chat_id=chat_id,
            )

        try:
            LOG.info("ws_open", chat_id=None)
            # ----- Phase 1: hello handshake -----
            try:
                first = await ws.recv()
            except ConnectionClosed:
                LOG.info("normal_close", chat_id=None, reason="client_gone_before_hello")
                return
            if isinstance(first, bytes):
                LOG.warning(
                    "auth_failed",
                    chat_id=None,
                    reason="wrong_first_frame",
                    detail="first frame was binary; expected text hello",
                )
                await ws.close(code=1002, reason="expected hello text frame")
                return
            try:
                hello = proto.parse_client(first)
            except ValueError as e:
                LOG.warning(
                    "auth_failed",
                    chat_id=None,
                    reason="malformed_hello",
                    detail=str(e),
                    byte_size=len(first),
                )
                await ws.close(code=1002, reason="malformed hello")
                return
            if hello.get("t") != "hello":
                LOG.warning(
                    "auth_failed",
                    chat_id=None,
                    reason="wrong_first_frame",
                    detail=f"first frame t={hello.get('t')!r}",
                )
                await ws.close(code=1002, reason="expected hello first")
                return

            client_token = hello.get("token", "")
            chat_id_candidate = str(hello.get("device") or "g2")
            LOG.info(
                "hello_received",
                chat_id=chat_id_candidate,
                has_token=bool(client_token),
                byte_size=len(first),
            )

            expected = self.cfg.token
            if not isinstance(client_token, str) or not hmac.compare_digest(
                client_token, expected,
            ):
                LOG.warning(
                    "auth_failed",
                    chat_id=chat_id_candidate,
                    reason="bad_token",
                )
                await ws.close(code=1008, reason="unauthorized")
                return

            LOG.info("auth_check", chat_id=chat_id_candidate, result="success")
            chat_id = chat_id_candidate
            await self.registry.register(chat_id, ws)
            LOG.info("registered", chat_id=chat_id)

            active = (
                self._active_session_lookup(chat_id)
                if self._active_session_lookup
                else None
            )
            caps = ["text", "voice", "tool-events", "sessions", "streaming"]
            await send_frame(proto.hello_ok(active=active, caps=caps), "hello.ok")

            # ----- Phase 2: frame dispatch loop -----
            LOG.info("dispatch_loop_enter", chat_id=chat_id)
            audio_buf = bytearray()
            capturing = False
            ping_task = asyncio.create_task(self._ping_loop(ws, chat_id))
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    LOG.debug(
                        "frame",
                        direction="in",
                        frame_type="binary",
                        byte_size=len(raw),
                        chat_id=chat_id,
                        capturing=capturing,
                    )
                    if capturing:
                        if len(audio_buf) + len(raw) > MAX_PCM_BYTES:
                            LOG.warning(
                                "pcm_cap_hit",
                                chat_id=chat_id,
                                byte_size=len(raw),
                                cap=MAX_PCM_BYTES,
                            )
                            audio_buf.extend(bytes(raw)[: MAX_PCM_BYTES - len(audio_buf)])
                            capturing = False
                        else:
                            audio_buf.extend(raw)
                    else:
                        LOG.debug(
                            "binary_frame_outside_capture",
                            chat_id=chat_id,
                            byte_size=len(raw),
                        )
                    continue

                try:
                    frame = proto.parse_client(raw)
                except ValueError as e:
                    LOG.warning(
                        "frame_decode_error",
                        chat_id=chat_id,
                        byte_size=len(raw),
                        error=str(e),
                        first_32_bytes_hex=raw[:32].hex() if isinstance(raw, str) else "",
                    )
                    continue

                frame_type = frame.get("t", "unknown")
                LOG.info(
                    "frame",
                    direction="in",
                    frame_type=frame_type,
                    byte_size=len(raw),
                    chat_id=chat_id,
                )

                match frame_type:
                    case "text":
                        text_content = str(frame.get("text", ""))
                        if self._on_text:
                            self._on_text(chat_id, text_content)
                    case "audio.start":
                        audio_buf.clear()
                        capturing = True
                        LOG.debug("audio_capture_start", chat_id=chat_id)
                    case "audio.stop":
                        capturing = False
                        pcm = bytes(audio_buf)
                        audio_buf.clear()
                        LOG.debug(
                            "audio_capture_stop",
                            chat_id=chat_id,
                            pcm_bytes=len(pcm),
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
                            "unknown_frame_type",
                            chat_id=chat_id,
                            frame_type=unknown_t,
                        )
            LOG.info("dispatch_loop_exit", chat_id=chat_id)
        except ConnectionClosed as e:
            LOG.info(
                "abnormal_close",
                chat_id=chat_id,
                code=getattr(e, "code", None),
                reason=getattr(e, "reason", None),
            )
        except Exception as e:
            LOG.exception(
                "handler_crash",
                chat_id=chat_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise
        finally:
            if ping_task is not None:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task
            if chat_id is not None:
                await self.registry.unregister(chat_id, ws)
            LOG.info("handler_exit", chat_id=chat_id)

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
