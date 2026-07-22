"""Pytest configuration and shared fixtures for the byoa_plugin test suite."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path
    from types import SimpleNamespace


@pytest.fixture
def reset_env() -> Iterator[dict[str, str]]:
    """Snapshot os.environ, clear it for the test, then restore after.

    Provides a clean-slate environment so tests don't leak secrets or
    settings between each other. Tests that need specific env values
    set them inside the test body.
    """
    snapshot = dict(os.environ)
    os.environ.clear()
    yield snapshot
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture
def hermes_home(tmp_path: Path) -> Path:
    """Override HERMES_HOME to a tmp dir so tests don't touch the real home."""
    home = tmp_path / ".hermes"
    home.mkdir()
    return home


@pytest.fixture
async def bridge_server(
    reset_env: dict[str, str],
    hermes_home: Path,
) -> AsyncIterator[SimpleNamespace]:
    """Spin up a real BridgeServer on an ephemeral port with capturing callbacks.

    Yields a SimpleNamespace with:
      - cfg: the BridgeConfig (token at cfg.token)
      - url: ws://127.0.0.1:<ephemeral-port>
      - received_text / received_audio / received_sessions_list / received_stop:
        lists capturing what the server routed to its callbacks

    The server runs for the lifetime of the fixture; teardown stops it.
    """
    from types import SimpleNamespace

    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry
    from byoa_plugin.server import BridgeServer

    cfg = BridgeConfig(
        token="integration-test-token",
        ws_host="127.0.0.1",
        ws_port=0,
    )
    registry = ConnectionRegistry()

    received_text: list[tuple[str, str]] = []
    received_audio: list[tuple[str, bytes]] = []
    received_sessions_list: list[str] = []
    received_stop: list[str] = []

    server = BridgeServer(
        cfg,
        registry,
        on_text=lambda cid, text: received_text.append((cid, text)),
        on_audio_stop=lambda cid, pcm: received_audio.append((cid, pcm)),
        on_sessions_list=received_sessions_list.append,
        on_stop=received_stop.append,
    )
    await server.start()

    actual_port = server.bound_port
    url = f"ws://127.0.0.1:{actual_port}"

    yield SimpleNamespace(
        cfg=cfg,
        url=url,
        received_text=received_text,
        received_audio=received_audio,
        received_sessions_list=received_sessions_list,
        received_stop=received_stop,
    )

    await server.stop()
