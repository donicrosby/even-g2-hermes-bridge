"""Integration tests: real WS server + fake glasses-app client.

Covers task group 10 from openspec/changes/build-even-g2-hermes-platform/tasks.md:
  - 10.2: invalid token → connection closed with code 1008 (policy violation)
  - 10.3: text frame → on_text callback invoked with (chat_id, content)
  - 10.5: unknown frame type → logged, connection stays open
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from tests.fake_client import FakeGlassesClient, parse_frame

if TYPE_CHECKING:
    from types import SimpleNamespace


class TestAuthHandshake:
    """Task 10.2: invalid token must close with code 1008."""

    async def test_invalid_token_closes_with_1008(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        async with FakeGlassesClient(url, token="wrong-token") as client:  # noqa: S106
            await client.send_hello()
            code, _reason = await client.expect_close(timeout=2.0)
        assert code == 1008

    async def test_valid_token_receives_hello_ok_with_caps(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            hello_ok = await client.recv_one(timeout=2.0)
        assert hello_ok is not None
        frame = parse_frame(hello_ok)
        assert frame["t"] == "hello.ok"
        assert "caps" in frame
        assert "streaming" in frame["caps"]


class TestTextFrameRouting:
    """Task 10.3: text frame routes to the adapter via on_text callback."""

    async def test_text_frame_invokes_on_text_callback(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            await client.recv_one(timeout=2.0)
            await client.send_text("hello world")
            await client.drain(timeout=0.3)
        assert bridge_server.received_text == [("test-g2", "hello world")]

    async def test_multiple_text_frames_each_route(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            await client.recv_one(timeout=2.0)
            await client.send_text("first")
            await client.send_text("second")
            await client.send_text("third")
            await client.drain(timeout=0.5)
        assert bridge_server.received_text == [
            ("test-g2", "first"),
            ("test-g2", "second"),
            ("test-g2", "third"),
        ]


class TestUnknownFrameTolerance:
    """Task 10.5: unknown frame type must be logged, not fatal."""

    async def test_unknown_frame_does_not_close_connection(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            await client.recv_one(timeout=2.0)

            await client.send_raw({"t": "totally-bogus", "payload": 42})
            await anyio.sleep(0.2)

            await client.send_text("still here")
            await client.drain(timeout=0.3)

        assert bridge_server.received_text == [("test-g2", "still here")]

    async def test_unknown_frame_then_known_frames_all_route(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            await client.recv_one(timeout=2.0)

            await client.send_text("before")
            await client.send_raw({"t": "what-is-this"})
            await client.send_text("after")
            await client.drain(timeout=0.5)

        assert bridge_server.received_text == [
            ("test-g2", "before"),
            ("test-g2", "after"),
        ]


class TestSessionsAndStopFrames:
    """Bonus coverage: sessions.list and stop also route to callbacks."""

    async def test_sessions_list_invokes_callback(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            await client.recv_one(timeout=2.0)
            await client.send_sessions_list()
            await client.drain(timeout=0.5)
        assert bridge_server.received_sessions_list == ["test-g2"]

    async def test_stop_frame_invokes_callback(
        self, bridge_server: SimpleNamespace,
    ) -> None:
        url = bridge_server.url
        token = bridge_server.cfg.token
        async with FakeGlassesClient(url, token=token) as client:
            await client.send_hello()
            await client.recv_one(timeout=2.0)
            await client.send_stop()
            await client.drain(timeout=0.5)
        assert bridge_server.received_stop == ["test-g2"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
