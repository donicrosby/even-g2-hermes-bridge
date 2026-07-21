"""Fake glasses-app WebSocket client for plugin integration tests.

Minimal async client speaking the even-g2 Protobuf wire protocol:
  - connect + send hello with a token
  - send text / audio.start / audio.stop / audio_data / arbitrary frames
  - capture server-pushed responses for assertions
  - observe close codes for auth-failure / protocol-violation tests
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import anyio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from byoa_plugin import wire
from byoa_plugin.proto_gen import hermes_bridge_pb2 as _pb

if TYPE_CHECKING:
    from collections.abc import Iterable


class FakeGlassesClient:
    """Async WS client emulating the glasses-app for plugin integration tests.

    Usage:

        async with FakeGlassesClient(url, token="...") as client:
            await client.send_hello()
            hello_ok = await client.recv_one()
            await client.send_text("hi")
            frames = await client.drain()
    """

    def __init__(
        self,
        url: str,
        *,
        token: str = "test-token",  # noqa: S107
        device: str = "test-g2",
    ) -> None:
        self.url = url
        self.token = token
        self.device = device
        self.received_frames: list[_pb.Frame] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._ws: Any = None

    async def connect(self) -> None:
        self._ws = await connect(self.url)

    async def __aenter__(self) -> FakeGlassesClient:  # noqa: PYI034
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def send_hello(self) -> None:
        await self._ws.send(wire.hello(self.token, self.device))

    async def send_text(self, text: str) -> None:
        await self._ws.send(wire.text(text))

    async def send_audio_start(self) -> None:
        await self._ws.send(wire.audio_start())

    async def send_audio_stop(self) -> None:
        await self._ws.send(wire.audio_stop())

    async def send_sessions_list(self) -> None:
        await self._ws.send(wire.sessions_list())

    async def send_sessions_switch(self, target: str) -> None:
        await self._ws.send(wire.sessions_switch(target))

    async def send_sessions_new(self) -> None:
        await self._ws.send(wire.sessions_new())

    async def send_stop(self) -> None:
        await self._ws.send(wire.stop())

    async def send_binary(self, data: bytes) -> None:
        await self._ws.send(wire.audio_data(data))

    async def drain(self, *, timeout: float = 0.3) -> list[_pb.Frame]:  # noqa: ASYNC109
        """Collect frames the server has pushed within timeout."""
        frames: list[_pb.Frame] = []
        try:
            with anyio.fail_after(timeout):
                while True:
                    raw = await self._ws.recv()
                    frames.append(wire.parse_frame(raw))
        except TimeoutError:
            pass
        self.received_frames.extend(frames)
        return frames

    async def recv_one(self, *, timeout: float = 1.0) -> _pb.Frame | None:  # noqa: ASYNC109
        try:
            with anyio.fail_after(timeout):
                raw = await self._ws.recv()
        except TimeoutError:
            return None
        frame = wire.parse_frame(raw)
        self.received_frames.append(frame)
        return frame

    async def expect_close(
        self, *, timeout: float = 1.0,  # noqa: ASYNC109
    ) -> tuple[int | None, str | None]:
        try:
            with anyio.fail_after(timeout):
                await self._ws.wait_closed()
        except TimeoutError:
            pass
        self.close_code = self._ws.close_code
        self.close_reason = self._ws.close_reason
        return self.close_code, self.close_reason

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed):
                await self._ws.close()
            self._ws = None


def parse_frame(raw: bytes) -> _pb.Frame:
    """Parse raw WS bytes into a Frame protobuf."""
    return wire.parse_frame(raw)


def frames_of_type(frames: Iterable[_pb.Frame], kind: str) -> list[_pb.Frame]:
    """Filter decoded frames by their oneof payload kind."""
    return [f for f in frames if f.WhichOneof("payload") == kind]
