"""HTTP endpoints multiplexed alongside the WebSocket server.

The websockets library lets a `process_request` callback handle non-WS HTTP
requests on the same port. This module owns those endpoints:

  GET /health  → JSON status (bind address, advertised URL, active chat_ids)
  GET /qr      → PNG image of the bootstrap QR code (delegates to qr_setup)

Unknown paths fall through to None so the websockets library rejects them.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from websockets.http11 import Request, Response

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry

LOG = logging.getLogger("byoa_plugin.http_endpoints")


class HttpEndpointHandler:
    """Callable handler for multiplexed HTTP endpoints on the WS port.

    websockets ≥ 13 calls `process_request(connection, request)`. We ignore
    the connection (we only need the cfg + registry) and route on the path.
    """

    def __init__(self, cfg: BridgeConfig, registry: ConnectionRegistry) -> None:
        """Store the bridge config and active connection registry."""
        self.cfg = cfg
        self.registry = registry

    async def __call__(
        self,
        _connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        """Route HTTP requests to the known health and QR endpoints."""
        path = request.path
        match path:
            case "/health":
                return self._health_response()
            case p if p == "/qr" or p.startswith("/qr?"):
                return self._qr_response()
        return None

    def _health_response(self) -> Response:
        body = json.dumps(
            {
                "status": "ok",
                "mode": "even-g2",
                "bind": f"{self.cfg.ws_host}:{self.cfg.ws_port}",
                "advertised_url": self.cfg.advertised_url,
                "token": (
                    self.cfg.token[:4] + "..." if self.cfg.token else "(unset)"
                ),
                "chat_ids": self.registry.active_chat_ids(),
            },
        ).encode("utf-8")
        return Response(
            status_code=200,
            reason_phrase="OK",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Cache-Control": "no-cache",
            },
            body=body,
        )

    def _qr_response(self) -> Response:
        from byoa_plugin.qr_setup import build_payload, generate_png  # noqa: PLC0415

        payload = build_payload(self.cfg.advertised_url, self.cfg.token)
        png = generate_png(payload)
        return Response(
            status_code=200,
            reason_phrase="OK",
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(len(png)),
                "Cache-Control": "no-cache",
            },
            body=png,
        )

