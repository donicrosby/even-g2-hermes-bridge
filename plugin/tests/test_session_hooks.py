"""Tests for the session lifecycle hook handlers in byoa_plugin.hooks.

Verifies that the four `on_session_*` handlers correctly:
  - attribute events to the most recent inbound chat_id,
  - mutate `_session_by_chat` so `chat_for_session` keeps working for
    tool-call routing,
  - emit (or don't emit) the right frames via the helper plumbing.

Frame-emission plumbing itself (`_emit_active_frame`'s asyncio path) is
not exercised here — we patch `_emit_active_frame` and assert call args.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from byoa_plugin import hooks


def _make_adapter(
    *,
    last_chat_id: str | None = "g2-1",
    session_by_chat: dict[str, str] | None = None,
) -> SimpleNamespace:
    """Build a minimal adapter stand-in that satisfies the hook handlers."""
    return SimpleNamespace(
        _last_chat_id=last_chat_id,
        _session_by_chat=session_by_chat or {},
        _bg_tasks=set(),
        registry=SimpleNamespace(send_frame=AsyncMock()),
    )


@pytest.fixture(autouse=True)
def _reset_adapter_singleton() -> None:
    """Clear the module-level _ADAPTER before each test."""
    hooks._ADAPTER = None
    yield
    hooks._ADAPTER = None


class TestOnSessionStart:
    def test_binds_session_and_emits_active_frame(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _make_adapter(last_chat_id="g2-1")
        hooks.set_adapter(adapter)
        captured: list[tuple[str, str]] = []
        monkeypatch.setattr(
            hooks,
            "_emit_active_frame",
            lambda adb, cid, sid: captured.append((cid, sid)),
        )

        hooks._on_session_start(session_id="s-abc", model="m", platform="even-g2")

        assert adapter._session_by_chat["g2-1"] == "s-abc"
        assert captured == [("g2-1", "s-abc")]

    def test_no_last_chat_id_skips_silently(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = _make_adapter(last_chat_id=None)
        hooks.set_adapter(adapter)
        emit = MagicMock()
        monkeypatch.setattr(hooks, "_emit_active_frame", emit)

        with caplog.at_level("DEBUG"):
            hooks._on_session_start(session_id="s-1", model="m", platform="even-g2")

        emit.assert_not_called()
        assert adapter._session_by_chat == {}
        assert "no last_chat_id" in caplog.text

    def test_no_adapter_set_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        emit = MagicMock()
        monkeypatch.setattr(hooks, "_emit_active_frame", emit)
        hooks._on_session_start(session_id="s-1", model="m", platform="even-g2")
        emit.assert_not_called()


class TestOnSessionReset:
    def test_behaves_identically_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _make_adapter(last_chat_id="g2-1")
        hooks.set_adapter(adapter)
        captured: list[tuple[str, str]] = []
        monkeypatch.setattr(
            hooks,
            "_emit_active_frame",
            lambda adb, cid, sid: captured.append((cid, sid)),
        )

        hooks._on_session_reset(session_id="s-def", platform="even-g2")

        assert adapter._session_by_chat["g2-1"] == "s-def"
        assert captured == [("g2-1", "s-def")]


class TestOnSessionEnd:
    def test_logs_without_emitting_frame(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = _make_adapter()
        hooks.set_adapter(adapter)
        emit = MagicMock()
        monkeypatch.setattr(hooks, "_emit_active_frame", emit)

        with caplog.at_level("INFO"):
            hooks._on_session_end(
                session_id="s-1",
                completed=True,
                interrupted=False,
                model="claude",
                platform="even-g2",
            )

        emit.assert_not_called()
        assert adapter._session_by_chat == {}
        assert "session end" in caplog.text
        assert "s-1" in caplog.text


class TestOnSessionFinalize:
    def test_removes_reverse_mapping(self) -> None:
        adapter = _make_adapter(session_by_chat={"g2-1": "s-1"})
        hooks.set_adapter(adapter)

        hooks._on_session_finalize(session_id="s-1", platform="even-g2")

        assert adapter._session_by_chat == {}

    def test_session_id_none_is_noop(self) -> None:
        adapter = _make_adapter(session_by_chat={"g2-1": "s-1"})
        hooks.set_adapter(adapter)

        hooks._on_session_finalize(session_id=None, platform="even-g2")

        assert adapter._session_by_chat == {"g2-1": "s-1"}

    def test_unknown_session_id_is_noop(self) -> None:
        adapter = _make_adapter(session_by_chat={"g2-1": "s-1"})
        hooks.set_adapter(adapter)

        hooks._on_session_finalize(session_id="s-other", platform="even-g2")

        assert adapter._session_by_chat == {"g2-1": "s-1"}


class TestResolveChatId:
    def test_returns_last_chat_id_when_set(self) -> None:
        adapter = _make_adapter(last_chat_id="g2-1")
        assert hooks._resolve_chat_id(adapter) == "g2-1"

    def test_returns_none_and_logs_when_unset(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = _make_adapter(last_chat_id=None)
        with caplog.at_level("DEBUG"):
            assert hooks._resolve_chat_id(adapter) is None
        assert "no last_chat_id" in caplog.text

    def test_tolerates_adapter_without_attr(self) -> None:
        adapter = SimpleNamespace()
        assert hooks._resolve_chat_id(adapter) is None
