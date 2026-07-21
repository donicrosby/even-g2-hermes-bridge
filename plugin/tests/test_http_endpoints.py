"""Tests for HttpEndpointHandler — multiplexed HTTP endpoints on the WS port.

Covers the /health JSON status, /qr PNG, and unknown-path fallthrough.
Extracted from server.py; these tests pin its contract independently.
"""

from __future__ import annotations

import json

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from byoa_plugin.config import BridgeConfig
from byoa_plugin.connections import ConnectionRegistry
from byoa_plugin.http_endpoints import HttpEndpointHandler


def _request(path: str) -> Request:
    """Build a minimal Request with the given path."""
    return Request(path=path, headers=Headers())


@pytest.fixture
def handler() -> HttpEndpointHandler:
    cfg = BridgeConfig.from_env()
    registry = ConnectionRegistry()
    return HttpEndpointHandler(cfg, registry)


class TestHealthEndpoint:
    async def test_returns_response_with_200(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        assert resp.status_code == 200

    async def test_content_type_is_json(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        assert resp.headers.get("Content-Type") == "application/json"

    async def test_body_contains_required_fields(
        self, handler: HttpEndpointHandler,
    ) -> None:
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        body = json.loads(resp.body.decode("utf-8"))
        assert body["status"] == "ok"
        assert body["mode"] == "even-g2"
        assert "bind" in body
        assert "advertised_url" in body
        assert "token" in body
        assert "chat_ids" in body

    async def test_token_masked_when_set(
        self, handler: HttpEndpointHandler,
    ) -> None:
        handler.cfg.token = "abcd-EF-1234567890"  # noqa: S105
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        body = json.loads(resp.body.decode("utf-8"))
        assert body["token"].startswith("abcd")
        assert "..." in body["token"]
        assert "EF-1234567890" not in body["token"]

    async def test_token_shows_unset_when_empty(
        self, handler: HttpEndpointHandler,
    ) -> None:
        handler.cfg.token = ""
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        body = json.loads(resp.body.decode("utf-8"))
        assert body["token"] == "(unset)"  # noqa: S105

    async def test_chat_ids_reflect_active_registrations(
        self, handler: HttpEndpointHandler,
    ) -> None:
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        body = json.loads(resp.body.decode("utf-8"))
        assert body["chat_ids"] == []

    async def test_cache_control_no_cache(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/health"))
        assert resp is not None
        assert resp.headers.get("Cache-Control") == "no-cache"


class TestQrEndpoint:
    async def test_returns_png_response(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/qr"))
        assert resp is not None
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type") == "image/png"

    async def test_body_is_nonempty_png(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/qr"))
        assert resp is not None
        assert len(resp.body) > 0
        assert resp.body[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_qr_with_query_string(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/qr?v=2"))
        assert resp is not None
        assert resp.status_code == 200


class TestUnknownPaths:
    async def test_unknown_path_returns_none(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/unknown"))
        assert resp is None

    async def test_root_returns_none(self, handler: HttpEndpointHandler) -> None:
        resp = await handler(object(), _request("/"))
        assert resp is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

