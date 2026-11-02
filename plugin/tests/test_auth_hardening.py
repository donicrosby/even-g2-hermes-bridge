"""Hardening tests: an unset bridge token must never yield an open WS endpoint.

Follow-up hardening for the JSON wire revert lane (pre-existing hole, not a
revert regression):

  - A server whose ``cfg.token`` is empty must refuse every WS handshake with
    code 1008 and log ``auth_failed reason=server_token_unset`` — otherwise
    ``hmac.compare_digest("", "")`` passes and any client gets agent turns.
  - The plugin must refuse to start its WS endpoint when the token is unset.
  - ``GET /health`` must report a boolean ``token_set`` instead of leaking a
    token prefix.

Each test is red against the pre-fix code (see the run recorded in the task).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

from fake_client import FakeGlassesClient

if TYPE_CHECKING:
    import pytest


async def _start_server(token: str) -> SimpleNamespace:
    """Start a real BridgeServer on an ephemeral port and return its handles."""
    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry
    from byoa_plugin.server import BridgeServer

    cfg = BridgeConfig(token=token, ws_host="127.0.0.1", ws_port=0)
    registry = ConnectionRegistry()
    server = BridgeServer(cfg, registry)
    await server.start()
    return SimpleNamespace(
        cfg=cfg,
        registry=registry,
        server=server,
        url=f"ws://127.0.0.1:{server.bound_port}",
    )


class TestEmptyServerTokenRefused:
    """A server with no configured token must refuse every WS handshake."""

    async def test_hello_with_empty_token_is_rejected(
        self,
        reset_env: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ns = await _start_server("")
        try:
            with caplog.at_level(logging.WARNING, logger="byoa_plugin"):
                async with FakeGlassesClient(ns.url, token="") as client:
                    await client.send_hello()
                    code, _reason = await client.expect_close(timeout=2.0)
            assert code == 1008
            assert "server_token_unset" in caplog.text
            assert ns.registry.active_chat_ids() == []
        finally:
            await ns.server.stop()

    async def test_missing_token_field_is_rejected_when_server_token_empty(
        self,
        reset_env: dict[str, str],
    ) -> None:
        # A hello that omits `token` entirely must not slip through either.
        import json

        ns = await _start_server("")
        try:
            async with FakeGlassesClient(ns.url) as client:
                await client._ws.send(
                    json.dumps({"t": "hello", "device": "test-g2"}),
                )
                code, _reason = await client.expect_close(timeout=2.0)
            assert code == 1008
            assert ns.registry.active_chat_ids() == []
        finally:
            await ns.server.stop()

    async def test_unset_env_token_rejected_at_load(
        self,
        reset_env: dict[str, str],
    ) -> None:
        """With no EVEN_G2_BRIDGE_TOKEN in the env, the loaded config refuses traffic."""
        from byoa_plugin.config import BridgeConfig

        loaded = BridgeConfig.from_env()
        assert loaded.token == ""

        ns = await _start_server(loaded.token)
        try:
            async with FakeGlassesClient(ns.url, token="") as client:
                await client.send_hello()
                code, _reason = await client.expect_close(timeout=2.0)
            assert code == 1008
            assert ns.registry.active_chat_ids() == []
        finally:
            await ns.server.stop()


class TestAdapterStartRefused:
    """The plugin must refuse to start serving when the token is unset."""

    async def test_connect_refuses_when_token_unset(
        self,
        reset_env: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from byoa_plugin.adapter import EvenG2Adapter

        adapter = EvenG2Adapter(SimpleNamespace(extra={}))
        # The isolated stub base class does not define gateway_runner; set it so
        # the pre-fix path completes and returns True (a clean red), rather than
        # aborting on AttributeError.
        adapter.gateway_runner = None
        with caplog.at_level(logging.ERROR, logger="byoa_plugin"):
            started = await adapter.connect()
        assert started is False
        assert adapter._server is None
        assert "refusing to start" in caplog.text

    async def test_connect_starts_when_token_set(
        self,
        reset_env: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from byoa_plugin.adapter import EvenG2Adapter

        monkeypatch.setenv("EVEN_G2_BRIDGE_TOKEN", "set-token")
        adapter = EvenG2Adapter(SimpleNamespace(extra={}))
        adapter.gateway_runner = None
        try:
            started = await adapter.connect()
            assert started is True
            assert adapter._server is not None
        finally:
            await adapter.disconnect()


class TestHealthTokenSet:
    """`/health` reports a boolean token_set, never a token prefix."""

    async def test_health_reports_token_set_true(
        self,
        reset_env: dict[str, str],
    ) -> None:
        import aiohttp

        ns = await _start_server("secret-token")
        try:
            url = f"http://127.0.0.1:{ns.server.bound_port}/health"
            async with aiohttp.ClientSession() as session, session.get(url) as resp:
                body = await resp.json()
            assert body["token_set"] is True
            assert "token" not in body
        finally:
            await ns.server.stop()

    async def test_health_reports_token_set_false_when_unset(
        self,
        reset_env: dict[str, str],
    ) -> None:
        import aiohttp

        ns = await _start_server("")
        try:
            url = f"http://127.0.0.1:{ns.server.bound_port}/health"
            async with aiohttp.ClientSession() as session, session.get(url) as resp:
                body = await resp.json()
            assert body["token_set"] is False
            assert "token" not in body
        finally:
            await ns.server.stop()
