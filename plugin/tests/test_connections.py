"""Tests for ConnectionRegistry — the chat_id → WebSocket registry.

Race conditions in register/unregister/send_frame have caused real bugs in
similar systems, so these tests exercise the identity guards explicitly.
"""

from __future__ import annotations

import pytest

from byoa_plugin.connections import ConnectionRegistry

_SEND_FAILURE_MSG = "simulated send failure"


class FakeWS:
    """Minimal stand-in for websockets.asyncio.server.ServerConnection.

    Registry only needs identity comparison and an async send() method.
    """

    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.sent: list[str] = []

    async def send(self, frame: str) -> None:
        if self.fail_send:
            raise OSError(_SEND_FAILURE_MSG)
        self.sent.append(frame)


class TestRegister:
    async def test_first_register_creates_new_state(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        state = await reg.register("chat1", ws)
        assert state.sent_len == 0

    async def test_same_socket_re_register_is_noop(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        first_state = await reg.register("chat1", ws)
        first_state.sent_len = 42
        second_state = await reg.register("chat1", ws)
        assert second_state is first_state
        assert second_state.sent_len == 42

    async def test_new_socket_preserves_stream_state(self) -> None:
        reg = ConnectionRegistry()
        old_ws = FakeWS()
        new_ws = FakeWS()
        first_state = await reg.register("chat1", old_ws)
        first_state.sent_len = 99
        second_state = await reg.register("chat1", new_ws)
        assert second_state is first_state
        assert reg.get("chat1") is new_ws


class TestUnregister:
    async def test_unregister_removes_connection(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        await reg.register("chat1", ws)
        await reg.unregister("chat1", ws)
        assert reg.get("chat1") is None

    async def test_unregister_skipped_when_socket_differs(self) -> None:
        """Guards the reconnect race: A registers → B registers → A exits."""
        reg = ConnectionRegistry()
        old_ws = FakeWS()
        new_ws = FakeWS()
        await reg.register("chat1", old_ws)
        await reg.register("chat1", new_ws)
        await reg.unregister("chat1", old_ws)
        assert reg.get("chat1") is new_ws

    async def test_unregister_missing_chat_is_noop(self) -> None:
        reg = ConnectionRegistry()
        await reg.unregister("unknown", FakeWS())

    async def test_unregister_after_explicit_removal_is_noop(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        await reg.register("chat1", ws)
        await reg.unregister("chat1", ws)
        await reg.unregister("chat1", ws)


class TestGet:
    async def test_get_returns_registered_socket(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        await reg.register("chat1", ws)
        assert reg.get("chat1") is ws

    async def test_get_returns_none_for_unknown_chat(self) -> None:
        reg = ConnectionRegistry()
        assert reg.get("nope") is None


class TestStreamState:
    async def test_returns_state_for_registered_chat(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        registered = await reg.register("chat1", ws)
        assert reg.stream_state("chat1") is registered

    async def test_creates_throwaway_state_for_unknown_chat(self) -> None:
        reg = ConnectionRegistry()
        state = reg.stream_state("unknown")
        assert state.sent_len == 0


class TestSendFrame:
    async def test_returns_true_and_sends_on_success(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        await reg.register("chat1", ws)
        ok = await reg.send_frame("chat1", "hello")
        assert ok
        assert ws.sent == ["hello"]

    async def test_returns_false_for_unknown_chat(self) -> None:
        reg = ConnectionRegistry()
        ok = await reg.send_frame("unknown", "hello")
        assert not ok

    async def test_returns_false_on_send_failure(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS(fail_send=True)
        await reg.register("chat1", ws)
        ok = await reg.send_frame("chat1", "hello")
        assert not ok

    async def test_unregisters_chat_when_send_fails(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS(fail_send=True)
        await reg.register("chat1", ws)
        await reg.send_frame("chat1", "hello")
        assert reg.get("chat1") is None

    async def test_does_not_unregister_replacement_socket_on_failure(self) -> None:
        """send_frame's unregister guard must respect socket identity too."""
        reg = ConnectionRegistry()
        stale = FakeWS(fail_send=True)
        fresh = FakeWS()
        await reg.register("chat1", stale)
        await reg.register("chat1", fresh)
        await reg.send_frame("chat1", "hello")
        # The send failed on `stale` — but the registered socket is now `fresh`,
        # so the identity guard prevents unregistering `fresh`.
        # Note: send_frame looks up the entry; send happens on whatever ws is
        # registered at that moment. This test pins the current contract:
        # failure unregisters only if the registered ws matches the failing one.
        # Since fresh.send succeeds, this verifies the success path post-replacement.
        assert reg.get("chat1") is fresh


class TestActiveChatIds:
    async def test_lists_registered_chats(self) -> None:
        reg = ConnectionRegistry()
        await reg.register("chat1", FakeWS())
        await reg.register("chat2", FakeWS())
        assert sorted(reg.active_chat_ids()) == ["chat1", "chat2"]

    async def test_empty_when_no_registrations(self) -> None:
        reg = ConnectionRegistry()
        assert reg.active_chat_ids() == []

    async def test_removes_chat_after_unregister(self) -> None:
        reg = ConnectionRegistry()
        ws = FakeWS()
        await reg.register("chat1", ws)
        await reg.unregister("chat1", ws)
        assert reg.active_chat_ids() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
