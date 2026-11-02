"""Regression tests pinning the BYOA chat-id key alignment (bug t_4c51c754).

``_dispatch_byoa_turn`` registers the BYOA future under the *resolved* chat id
(``adapter._last_chat_id or _BYOA_CHAT_ID``) but, pre-fix, dispatched the
``MessageEvent`` under the constant ``_BYOA_CHAT_ID``. Final delivery resolves
the future by popping ``_byoa_futures[event.source.chat_id]`` (adapter.py:377),
so a stale sticky key (e.g. the WS debug client's ``debug-client``) made the pop
miss -> the future never resolved -> 502 at the 120s timeout.

These tests pin every key site to the resolved id: the future's registration
key, the dispatched event's chat id, the timeout cancellation, and the
``byoa_turn_start`` log line.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from byoa_plugin.adapter import MessageEvent


class _KeyPinAdapter:
    """Mock adapter that mirrors the real future-resolution semantics.

    ``handle_message`` mimics the gateway's final ``send()`` by popping the
    BYOA future under the *dispatched event's* chat id only -- exactly the
    lookup ``adapter.py`` performs -- so a register/dispatch key mismatch
    leaves the future unresolved and the turn times out.
    """

    def __init__(self, *, last_chat_id: str | None) -> None:
        self._last_chat_id = last_chat_id
        self._futures: dict[str, asyncio.Future[str]] = {}
        self.registered_chat_id: str | None = None
        self.dispatched_chat_id: str | None = None
        self.cancelled_chat_id: str | None = None
        self.resolve = True

    @property
    def platform(self) -> object:
        return SimpleNamespace(value="even-g2")

    def build_source(self, *, chat_id: str, user_id: str) -> SimpleNamespace:
        return SimpleNamespace(chat_id=chat_id, user_id=user_id)

    def register_byoa_future(self, chat_id: str) -> asyncio.Future[str]:
        self.registered_chat_id = chat_id
        fut = asyncio.get_running_loop().create_future()
        self._futures[chat_id] = fut
        return fut

    def cancel_byoa_future(self, chat_id: str) -> None:
        self.cancelled_chat_id = chat_id
        fut = self._futures.pop(chat_id, None)
        if fut and not fut.done():
            fut.cancel()

    async def handle_message(self, event: MessageEvent) -> None:
        self.dispatched_chat_id = event.source.chat_id
        if not self.resolve:
            return
        fut = self._futures.pop(event.source.chat_id, None)
        if fut is not None and not fut.done():
            fut.set_result("aligned")


def _server_with(adapter: object) -> SimpleNamespace:
    """Build a BridgeServer on an unstarted port with ``adapter`` wired in."""
    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry
    from byoa_plugin.server import BridgeServer

    cfg = BridgeConfig(token="ws", ws_host="127.0.0.1", ws_port=0)
    server = BridgeServer(cfg, ConnectionRegistry())
    server.set_adapter(adapter)
    return SimpleNamespace(server=server)


async def test_dispatch_keys_aligned_when_last_chat_id_is_stale(
    monkeypatch: object,
) -> None:
    """A foreign sticky key must be used for BOTH registration and dispatch."""
    from byoa_plugin import server as server_mod

    adapter = _KeyPinAdapter(last_chat_id="debug-client")
    server = _server_with(adapter).server
    # Bound the wait so a pre-fix key mismatch fails fast instead of blocking
    # for the production 120s timeout.
    monkeypatch.setattr(server_mod, "_BYOA_TIMEOUT_SEC", 0.5)  # type: ignore[attr-defined]

    content = await server._dispatch_byoa_turn("hello")

    assert content == "aligned"
    assert adapter.registered_chat_id == "debug-client"
    assert adapter.dispatched_chat_id == "debug-client"


async def test_dispatch_keys_aligned_when_no_sticky_chat_id(
    monkeypatch: object,
) -> None:
    """With no sticky key, both sites fall back to the BYOA constant."""
    from byoa_plugin import server as server_mod

    adapter = _KeyPinAdapter(last_chat_id=None)
    server = _server_with(adapter).server
    monkeypatch.setattr(server_mod, "_BYOA_TIMEOUT_SEC", 0.5)  # type: ignore[attr-defined]

    content = await server._dispatch_byoa_turn("hello")

    assert content == "aligned"
    assert adapter.registered_chat_id == "even-add-agent"
    assert adapter.dispatched_chat_id == "even-add-agent"


async def test_timeout_cancels_future_under_resolved_chat_id(
    monkeypatch: object,
) -> None:
    """The timeout path must cancel the future under the resolved chat id."""
    from byoa_plugin import server as server_mod

    adapter = _KeyPinAdapter(last_chat_id="debug-client")
    adapter.resolve = False
    server = _server_with(adapter).server
    monkeypatch.setattr(server_mod, "_BYOA_TIMEOUT_SEC", 0.05)  # type: ignore[attr-defined]

    result = await server._dispatch_byoa_turn("hello")

    assert result is None
    assert adapter.cancelled_chat_id == "debug-client"


async def test_turn_start_logs_resolved_chat_id(
    caplog: object,
    monkeypatch: object,
) -> None:
    """``byoa_turn_start`` must report the resolved chat id, not the constant."""
    from byoa_plugin import server as server_mod

    adapter = _KeyPinAdapter(last_chat_id="debug-client")
    server = _server_with(adapter).server
    monkeypatch.setattr(server_mod, "_BYOA_TIMEOUT_SEC", 0.5)  # type: ignore[attr-defined]

    with caplog.at_level("INFO", logger="byoa_plugin.server"):  # type: ignore[attr-defined]
        await server._dispatch_byoa_turn("hello")

    starts = [
        r.getMessage()
        for r in caplog.records  # type: ignore[attr-defined]
        if r.getMessage().startswith("byoa_turn_start")
    ]
    assert starts
    assert "chat_id=debug-client" in starts[0]
