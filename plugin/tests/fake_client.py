"""Fake glasses-app WebSocket client for plugin integration tests.

Minimal async client speaking the even_g2 wire protocol:
  - connect + send hello with a token
  - send text / audio.start / audio.stop / binary / arbitrary frames
  - capture server-pushed responses for assertions
  - observe close codes for auth-failure / protocol-violation tests
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

import anyio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

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

    The context manager handles connect/close. Manual lifecycle is also
    supported via `await client.connect()` and `await client.close()`.
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
        self.received_frames: list[str] = []
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
        await self._send({"t": "hello", "token": self.token, "device": self.device})

    async def send_text(self, text: str) -> None:
        await self._send({"t": "text", "text": text})

    async def send_audio_start(self) -> None:
        await self._send({"t": "audio.start"})

    async def send_audio_stop(self) -> None:
        await self._send({"t": "audio.stop"})

    async def send_sessions_list(self) -> None:
        await self._send({"t": "sessions.list"})

    async def send_stop(self) -> None:
        await self._send({"t": "stop"})

    async def send_binary(self, data: bytes) -> None:
        await self._ws.send(data)

    async def send_raw(self, frame: dict[str, Any]) -> None:
        await self._send(frame)

    async def drain(self, *, timeout: float = 0.3) -> list[str]:  # noqa: ASYNC109
        """Collect frames the server has pushed within timeout.

        Returns whatever arrived (possibly empty). Does not raise on timeout.
        """
        frames: list[str] = []
        try:
            with anyio.fail_after(timeout):
                while True:
                    frame = await self._ws.recv()
                    frames.append(self._decode(frame))
        except TimeoutError:
            pass
        self.received_frames.extend(frames)
        return frames

    async def recv_one(self, *, timeout: float = 1.0) -> str | None:  # noqa: ASYNC109
        try:
            with anyio.fail_after(timeout):
                frame = await self._ws.recv()
        except TimeoutError:
            return None
        decoded = self._decode(frame)
        self.received_frames.append(decoded)
        return decoded

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

    async def _send(self, frame: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(frame))

    @staticmethod
    def _decode(frame: str | bytes) -> str:
        if isinstance(frame, bytes):
            return frame.decode("utf-8", errors="replace")
        return frame


def parse_frame(raw: str) -> dict[str, Any]:
    """Parse a JSON frame string into a dict (mirrors protocol.parse_client)."""
    return json.loads(raw)


def frames_of_type(frames: Iterable[str], frame_type: str) -> list[dict[str, Any]]:
    """Filter decoded frames by their `t` field."""
    return [f for f in (parse_frame(r) for r in frames) if f.get("t") == frame_type]
