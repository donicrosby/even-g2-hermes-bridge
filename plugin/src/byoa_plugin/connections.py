"""Connection registry and StreamState for the even-g2 plugin.

ConnectionRegistry: maps chat_id → active WebSocket connection. Provides
thread-safe register/unregister/send_frame operations.

StreamState: per-chat cursor that tracks how many characters of the
accumulated assistant text have already been sent to the client. Used to
compute delta-only updates so the gateway can call edit_message with the
full accumulated text and the adapter sends only the new suffix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from byoa_plugin import protocol as proto
from websockets.exceptions import ConnectionClosed

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

LOG = logging.getLogger("byoa_plugin.connections")

STREAMING_CURSOR = proto.STREAMING_CURSOR


@dataclass
class StreamState:
    """Tracks sent_len so delta_for() returns only the unsent suffix.

    The Hermes Gateway sends the full accumulated text on every edit_message
    call (it doesn't stream token-by-token to platforms). We diff against
    what we've already pushed to the client and send only the new chars.
    """

    sent_len: int = 0

    def delta_for(self, accumulated: str) -> str:
        """Return the unsent suffix of `accumulated`.

        Strips the trailing STREAMING_CURSOR (' ▉') before diffing so the
        cursor the gateway appends to in-progress text doesn't leak into
        deltas.
        """
        clean = accumulated
        clean = clean.removesuffix(STREAMING_CURSOR)
        if len(clean) < self.sent_len:
            # Content shrank — treat as fresh. Reset cursor and send full text.
            self.sent_len = len(clean)
            return clean
        delta = clean[self.sent_len:]
        self.sent_len = len(clean)
        return delta

    def reset(self) -> None:
        self.sent_len = 0


class ConnectionRegistry:
    """Maps chat_id → (websocket, StreamState) for active connections.

    Guards against stale reconnects: if a new connection arrives for a
    chat_id that already has one, the old one is evicted only if the socket
    identity differs (matching the huntsyea reference pattern).
    """

    def __init__(self) -> None:
        self._conns: dict[str, tuple[ServerConnection, StreamState]] = {}
        self._lock = anyio.Lock()

    async def register(self, chat_id: str, ws: ServerConnection) -> StreamState:
        """Register a new connection. Returns the StreamState for this chat.

        If an existing connection exists for chat_id and its socket differs,
        the old socket is left to its own devices (the handler will exit and
        the websockets library will close it). The StreamState is preserved
        across reconnects so streaming deltas continue correctly.
        """
        async with self._lock:
            existing = self._conns.get(chat_id)
            if existing is not None and existing[0] is ws:
                # Same socket re-registering — no-op, return existing state.
                return existing[1]
            # New socket (or first registration).
            state = existing[1] if existing is not None else StreamState()
            self._conns[chat_id] = (ws, state)
            if existing is not None:
                LOG.info(
                    "replaced stale connection for chat_id=%s", chat_id,
                )
            else:
                LOG.info("registered chat_id=%s", chat_id)
            return state

    async def unregister(self, chat_id: str, ws: ServerConnection) -> None:
        """Unregister a connection only if `ws` matches the registered one.

        Prevents a reconnect race where:
          1. socket A registers
          2. socket B registers (replacing A)
          3. socket A's handler exits, calls unregister
        Without this guard, step 3 would evict socket B.
        """
        async with self._lock:
            existing = self._conns.get(chat_id)
            if existing is None:
                return
            if existing[0] is not ws:
                # Stale unregister — someone else has the slot now.
                LOG.debug(
                    "skipping stale unregister for chat_id=%s", chat_id,
                )
                return
            del self._conns[chat_id]
            LOG.info("unregistered chat_id=%s", chat_id)

    def get(self, chat_id: str) -> ServerConnection | None:
        entry = self._conns.get(chat_id)
        return entry[0] if entry is not None else None

    def stream_state(self, chat_id: str) -> StreamState:
        """Get-or-create a StreamState for the chat_id.

        Creating on demand is safe — a freshly-initialized StreamState with
        sent_len=0 produces the full accumulated text as the first delta,
        which is exactly what we want for a brand-new chat_id.
        """
        entry = self._conns.get(chat_id)
        if entry is not None:
            return entry[1]
        # No active connection — return a throwaway state so delta_for()
        # doesn't crash the caller. The computed delta is discarded.
        return StreamState()

    async def send_frame(self, chat_id: str, frame: str) -> bool:
        """Send a text frame to the chat_id's socket. Returns True on success.

        On send failure, unregisters the chat_id (the socket is broken).
        """
        entry = self._conns.get(chat_id)
        if entry is None:
            LOG.debug("send_frame: no socket for chat_id=%s", chat_id)
            return False
        ws, _ = entry
        try:
            await ws.send(frame)
            return True
        except (ConnectionClosed, OSError) as e:
            LOG.warning(
                "send_frame failed for chat_id=%s: %s — unregistering",
                chat_id,
                e,
            )
            async with self._lock:
                # Re-check identity before unregistering to avoid the same
                # race unregister() guards against.
                cur = self._conns.get(chat_id)
                if cur is not None and cur[0] is ws:
                    del self._conns[chat_id]
            return False

    def active_chat_ids(self) -> list[str]:
        return list(self._conns.keys())
