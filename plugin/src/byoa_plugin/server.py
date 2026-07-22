"""Unified WS + HTTP server for the even-g2 plugin.

Uses aiohttp for both surfaces on a single port (default 8767):

  WS   /                     → glasses-app Protobuf protocol (existing)
  GET  /health               → JSON status
  GET  /qr                   → PNG QR code for glasses-app bootstrap
  POST /v1/chat/completions  → BYOA endpoint for Even's Add Agent (OpenAI shape)
  POST /                     → alias for /v1/chat/completions

The server binds to BridgeConfig.ws_host:ws_port. External exposure is via
Tailscale Serve or a user-provided reverse proxy (see net.py and setup_flow.py).
"""

from __future__ import annotations

import asyncio
import gzip
import hmac
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from byoa_plugin import wire
from byoa_plugin.log import get_logger
from byoa_plugin.wire import FrameParseError

if TYPE_CHECKING:
    from collections.abc import Callable

    from byoa_plugin.adapter import EvenG2Adapter
    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry

LOG = get_logger("byoa_plugin.server")

MAX_PCM_BYTES = 8 * 1024 * 1024
PING_INTERVAL_SEC = 30
_BYOA_CHAT_ID = "even-add-agent"
_BYOA_TIMEOUT_SEC = 120.0


class BridgeServer:
    """Unified WS + HTTP server bridging glasses-app ↔ plugin."""

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
        self._on_text = on_text
        self._on_audio_stop = on_audio_stop
        self._on_sessions_list = on_sessions_list
        self._on_sessions_switch = on_sessions_switch
        self._on_sessions_new = on_sessions_new
        self._on_stop = on_stop
        self._active_session_lookup = active_session_lookup
        self._adapter: EvenG2Adapter | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def set_adapter(self, adapter: EvenG2Adapter) -> None:
        """Wire the adapter so the BYOA handler can dispatch turns."""
        self._adapter = adapter

    def _build_app(self) -> web.Application:
        app = web.Application(
            client_max_size=MAX_PCM_BYTES + 65536,
        )
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/qr", self._qr_handler)
        app.router.add_post("/v1/chat/completions", self._byoa_handler)
        app.router.add_post("/", self._byoa_handler)
        app.router.add_get("/", self._ws_handler)
        return app

    async def start(self) -> None:
        """Start the unified server."""
        host = self.cfg.ws_host
        port = self.cfg.ws_port
        LOG.info("WS server starting on %s:%s", host, port)
        app = self._build_app()
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        LOG.info("WS server listening on %s:%s", host, port)

    @property
    def bound_port(self) -> int:
        """Return the actual port the server is listening on."""
        if self._site is None or self._site._server is None:
            return 0
        sockets = self._site._server.sockets
        if not sockets:
            return 0
        return sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop the server and wait for connections to close."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        LOG.info("WS server stopped")

    # ---- WebSocket handler --------------------------------------------------

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:  # noqa: C901
        """Handle one WebSocket connection from hello handshake to teardown."""
        ws = web.WebSocketResponse(
            max_msg_size=MAX_PCM_BYTES + 65536,
            heartbeat=PING_INTERVAL_SEC,
        )
        await ws.prepare(request)

        chat_id: str | None = None

        try:
            LOG.info("ws_open", chat_id=None)

            try:
                msg = await ws.receive()
            except (ConnectionResetError, asyncio.CancelledError):
                LOG.info("normal_close", chat_id=None, reason="client_gone_before_hello")
                return ws

            if msg.type != WSMsgType.BINARY:
                LOG.warning("auth_failed", chat_id=None, reason="non_binary_first_frame")
                await ws.close(code=1002, message=b"expected binary frame")
                return ws

            first = msg.data
            try:
                hello_frame = wire.parse_frame(first)
            except FrameParseError as e:
                LOG.warning("auth_failed", chat_id=None, reason="malformed_hello", detail=str(e))
                await ws.close(code=1002, message=b"malformed hello")
                return ws

            kind = hello_frame.WhichOneof("payload")
            if kind != "hello":
                LOG.warning("auth_failed", chat_id=None, reason="wrong_first_frame", detail=f"kind={kind!r}")
                await ws.close(code=1002, message=b"expected hello first")
                return ws

            client_token = hello_frame.hello.token
            chat_id_candidate = hello_frame.hello.device or "g2"
            LOG.info("hello_received", chat_id=chat_id_candidate, has_token=bool(client_token))

            if not isinstance(client_token, str) or not hmac.compare_digest(client_token, self.cfg.token):
                LOG.warning("auth_failed", chat_id=chat_id_candidate, reason="bad_token")
                await ws.close(code=1008, message=b"unauthorized")
                return ws

            LOG.info("auth_check", chat_id=chat_id_candidate, result="success")
            chat_id = chat_id_candidate
            await self.registry.register(chat_id, ws)
            LOG.info("registered", chat_id=chat_id)

            active = self._active_session_lookup(chat_id) if self._active_session_lookup else None
            caps = ["text", "voice", "tool-events", "sessions", "streaming"]
            await self._send_frame(ws, wire.hello_ok(active=active, caps=caps), "hello.ok", chat_id)

            LOG.info("dispatch_loop_enter", chat_id=chat_id)
            audio_buf = bytearray()
            capturing = False

            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    LOG.warning("ws_error", chat_id=chat_id, error=str(ws.exception()))
                    break
                if msg.type != WSMsgType.BINARY:
                    continue

                raw = msg.data
                try:
                    frame = wire.parse_frame(raw)
                except FrameParseError as e:
                    LOG.warning("frame_decode_error", chat_id=chat_id, byte_size=len(raw), error=str(e))
                    continue

                fkind = frame.WhichOneof("payload")
                LOG.info("frame", direction="in", frame_type=fkind or "unknown", byte_size=len(raw), chat_id=chat_id)

                match fkind:
                    case "text" if self._on_text:
                        self._on_text(chat_id, frame.text.content)
                    case "audio_start":
                        audio_buf.clear()
                        capturing = True
                    case "audio_data" if capturing:
                        pcm = frame.audio_data.pcm
                        if len(audio_buf) + len(pcm) > MAX_PCM_BYTES:
                            LOG.warning("pcm_cap_hit", chat_id=chat_id, cap=MAX_PCM_BYTES)
                            audio_buf.extend(pcm[: MAX_PCM_BYTES - len(audio_buf)])
                            capturing = False
                        else:
                            audio_buf.extend(pcm)
                    case "audio_data":
                        LOG.debug("audio_data_outside_capture", chat_id=chat_id)
                    case "audio_stop":
                        capturing = False
                        pcm = bytes(audio_buf)
                        audio_buf.clear()
                        if self._on_audio_stop:
                            self._on_audio_stop(chat_id, pcm)
                    case "sessions_list" if self._on_sessions_list:
                        self._on_sessions_list(chat_id)
                    case "sessions_switch" if self._on_sessions_switch:
                        target = frame.sessions_switch.target or "+1"
                        self._on_sessions_switch(chat_id, target)
                    case "sessions_new" if self._on_sessions_new:
                        self._on_sessions_new(chat_id)
                    case "stop" if self._on_stop:
                        self._on_stop(chat_id)
                    case None:
                        LOG.warning("empty_frame", chat_id=chat_id)
                    case unknown:
                        LOG.warning("unknown_frame_type", chat_id=chat_id, frame_type=unknown)

            LOG.info("dispatch_loop_exit", chat_id=chat_id)
        except Exception as e:
            LOG.exception("handler_crash", chat_id=chat_id, error=str(e), error_type=type(e).__name__)
        finally:
            if chat_id is not None:
                await self.registry.unregister(chat_id, ws)
            LOG.info("handler_exit", chat_id=chat_id)

        return ws

    async def _send_frame(
        self, ws: web.WebSocketResponse, data: bytes, frame_type: str, chat_id: str,
    ) -> None:
        await ws.send_bytes(data)
        LOG.info("frame", direction="out", frame_type=frame_type, byte_size=len(data), chat_id=chat_id)

    # ---- HTTP handlers ------------------------------------------------------

    async def _health_handler(self, _request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "mode": "even-g2",
            "bind": f"{self.cfg.ws_host}:{self.cfg.ws_port}",
            "advertised_url": self.cfg.advertised_url,
            "token": self.cfg.token[:4] + "..." if self.cfg.token else "(unset)",
            "byoa_enabled": bool(self.cfg.byoa_token),
            "chat_ids": self.registry.active_chat_ids(),
        })

    async def _qr_handler(self, _request: web.Request) -> web.Response:
        from byoa_plugin.qr_setup import build_payload, generate_png  # noqa: PLC0415

        payload = build_payload(self.cfg.advertised_url, self.cfg.token)
        png = generate_png(payload)
        return web.Response(body=png, content_type="image/png")

    async def _byoa_handler(self, request: web.Request) -> web.Response:
        if not self.cfg.byoa_token:
            LOG.warning("byoa_request_rejected reason=disabled")
            return self._byoa_error(503, "BYOA endpoint disabled (BYOA_TOKEN not set)")

        auth = request.headers.get("authorization", "")
        expected = f"Bearer {self.cfg.byoa_token}"
        if not hmac.compare_digest(auth, expected):
            LOG.warning("byoa_auth_failed")
            return self._byoa_error(401, "unauthorized", "auth_error")

        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            LOG.warning("byoa_invalid_json")
            return self._byoa_error(400, "invalid JSON", "invalid_request_error")

        user_content = _latest_user_content(body)
        if user_content is None:
            LOG.warning("byoa_no_user_message")
            return self._byoa_error(400, "no user message", "invalid_request_error")

        if self._adapter is None:
            LOG.error("byoa_no_adapter")
            return self._byoa_error(503, "adapter not wired")

        content = await self._dispatch_byoa_turn(user_content)
        if content is None:
            return self._byoa_error(502, "upstream error", "upstream_error")

        return self._byoa_success(request, content)

    async def _dispatch_byoa_turn(self, user_content: str) -> str | None:
        adapter = self._adapter
        if adapter is None:
            return None

        fut = adapter.register_byoa_future(_BYOA_CHAT_ID)

        try:
            from byoa_plugin.adapter import MessageEvent, MessageType  # noqa: PLC0415

            event = MessageEvent(
                text=user_content,
                message_type=MessageType.TEXT,
                source=adapter.build_source(
                    chat_id=_BYOA_CHAT_ID,
                    user_id=adapter.platform.value or "byoa",
                ),
            )
            LOG.info("byoa_turn_start chat_id=%s len=%d", _BYOA_CHAT_ID, len(user_content))
            await adapter.handle_message(event)
            content = await asyncio.wait_for(fut, timeout=_BYOA_TIMEOUT_SEC)
            LOG.info("byoa_turn_complete chat_id=%s len=%d", _BYOA_CHAT_ID, len(content))
            return content
        except TimeoutError:
            LOG.warning("byoa_turn_timeout chat_id=%s", _BYOA_CHAT_ID)
            adapter.cancel_byoa_future(_BYOA_CHAT_ID)
            return None
        except Exception:
            LOG.exception("byoa_turn_failed chat_id=%s", _BYOA_CHAT_ID)
            adapter.cancel_byoa_future(_BYOA_CHAT_ID)
            return None

    def _byoa_success(self, request: web.Request, content: str) -> web.Response:
        body_dict = _chat_completion(content)
        accept = request.headers.get("accept-encoding", "")
        if "gzip" in accept and len(content) > 512:
            return web.json_response(body_dict, headers={"Content-Encoding": "gzip"}, dumps=lambda d: gzip.compress(json.dumps(d).encode()))
        return web.json_response(body_dict)

    def _byoa_error(self, status: int, message: str, etype: str = "invalid_request_error") -> web.Response:
        return web.json_response(
            {"error": {"message": message, "type": etype}},
            status=status,
        )


def _latest_user_content(body: dict[str, Any]) -> str | None:
    messages = body.get("messages") or []
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def _chat_completion(content: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-byoa-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "byoa-bridge",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            },
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content),
            "total_tokens": len(content),
        },
    }
