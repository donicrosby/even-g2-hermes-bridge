"""Integration tests for hello.ok active-session-id wiring.

Verifies that the BridgeServer includes the `active=<session_id>` field in
hello.ok when the `active_session_lookup` callback returns one, and omits
the field entirely when the callback returns None.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import SimpleNamespace


@pytest.fixture
async def bridge_server_with_lookup(
    reset_env: dict[str, str],
    hermes_home: object,
) -> AsyncIterator[SimpleNamespace]:
    """Spin up a BridgeServer with a known-session lookup.

    The lookup returns "s-known" for chat_id "g2-known" and None for any
    other chat_id, so we can exercise both branches of the hello.ok active
    field inclusion.
    """
    from types import SimpleNamespace

    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry
    from byoa_plugin.server import BridgeServer

    cfg = BridgeConfig(
        token="active-test-token",
        ws_host="127.0.0.1",
        ws_port=0,
    )
    registry = ConnectionRegistry()

    def lookup(chat_id: str) -> str | None:
        if chat_id == "g2-known":
            return "s-known"
        return None

    server = BridgeServer(
        cfg,
        registry,
        active_session_lookup=lookup,
    )
    await server.start()
    actual_port = server.bound_port
    url = f"ws://127.0.0.1:{actual_port}"
    yield SimpleNamespace(cfg=cfg, url=url)
    await server.stop()


class TestHelloOkActiveField:
    async def test_active_included_when_lookup_returns_session_id(
        self,
        bridge_server_with_lookup: SimpleNamespace,
    ) -> None:
        from fake_client import FakeGlassesClient

        url = bridge_server_with_lookup.url
        token = bridge_server_with_lookup.cfg.token
        async with FakeGlassesClient(url, token=token, device="g2-known") as client:
            await client.send_hello()
            frame = await client.recv_one(timeout=2.0)
        assert frame is not None
        assert frame.WhichOneof("payload") == "hello_ok"
        assert frame.hello_ok.active == "s-known"

    async def test_active_omitted_when_lookup_returns_none(
        self,
        bridge_server_with_lookup: SimpleNamespace,
    ) -> None:
        from fake_client import FakeGlassesClient

        url = bridge_server_with_lookup.url
        token = bridge_server_with_lookup.cfg.token
        async with FakeGlassesClient(url, token=token, device="g2-unknown") as client:
            await client.send_hello()
            frame = await client.recv_one(timeout=2.0)
        assert frame is not None
        assert frame.WhichOneof("payload") == "hello_ok"
        assert not frame.hello_ok.active
