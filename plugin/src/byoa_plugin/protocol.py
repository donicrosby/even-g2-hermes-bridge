"""Wire protocol for the even-g2 plugin ↔ glasses-app WebSocket.

Single source of truth for frame shapes. The build-time script
`protocol_gen.py` emits `glasses-app/src/protocol.ts` from this file so the
TypeScript side can import the same schema.

Frame families:
  Client → Server (inbound):
    hello, text, audio.start, audio.stop, sessions.list, sessions.switch,
    sessions.new, stop

  Server → Client (outbound):
    hello.ok, assistant.delta, assistant, tool.start, tool.end, turn.done,
    sessions, active, history, transcript, error

Binary frames between `audio.start` and `audio.stop` carry raw PCM16 LE 16kHz
mono audio bytes.
"""

from __future__ import annotations

import json
from typing import Any

# Streaming cursor appended by Hermes Gateway to in-progress accumulated text.
# Stripped by StreamState.delta_for() before diffing.
STREAMING_CURSOR = " ▉"


class ProtocolParseError(ValueError):
    """Base for malformed client frames."""


class MalformedJSONFrame(ProtocolParseError):
    """Client sent a frame that failed JSON parsing."""

    def __init__(self, original: Exception) -> None:
        self.original = original
        super().__init__(f"malformed JSON frame: {original}")


class MissingTField(ProtocolParseError):
    """Client frame parsed as JSON but lacks the required 't' discriminator."""

    def __init__(self) -> None:
        super().__init__("frame missing required 't' field")

# ---- Inbound frame constructors (client → server) -------------------------


def hello(token: str, device: str = "g2") -> str:
    return json.dumps({"t": "hello", "token": token, "device": device})


def text(content: str) -> str:
    return json.dumps({"t": "text", "text": content})


def audio_start() -> str:
    return json.dumps({"t": "audio.start"})


def audio_stop() -> str:
    return json.dumps({"t": "audio.stop"})


def sessions_list() -> str:
    return json.dumps({"t": "sessions.list"})


def sessions_switch(target: str) -> str:
    """Target is either a session id or "+1"/"-1" for relative switching."""
    return json.dumps({"t": "sessions.switch", "id": target})


def sessions_new() -> str:
    return json.dumps({"t": "sessions.new"})


def stop() -> str:
    return json.dumps({"t": "stop"})


# ---- Outbound frame constructors (server → client) ------------------------


def hello_ok(active: str | None = None, caps: list[str] | None = None) -> str:
    payload: dict[str, Any] = {"t": "hello.ok"}
    if active is not None:
        payload["active"] = active
    if caps is not None:
        payload["caps"] = caps
    return json.dumps(payload)


def assistant_delta(text: str) -> str:
    return json.dumps({"t": "assistant.delta", "text": text})


def assistant_full(text: str) -> str:
    return json.dumps({"t": "assistant", "text": text})


def tool_start(
    name: str, label: str | None = None, emoji: str | None = None,
) -> str:
    payload: dict[str, Any] = {"t": "tool.start", "name": name}
    if label:
        payload["label"] = label
    if emoji:
        payload["emoji"] = emoji
    return json.dumps(payload)


def tool_end(name: str, ok: bool = True) -> str:
    return json.dumps({"t": "tool.end", "name": name, "ok": ok})


def turn_done() -> str:
    return json.dumps({"t": "turn.done"})


def sessions(items: list[dict[str, Any]], active: str | None = None) -> str:
    payload: dict[str, Any] = {"t": "sessions", "items": items}
    if active is not None:
        payload["active"] = active
    return json.dumps(payload)


def active(session_id: str, name: str | None = None) -> str:
    payload: dict[str, Any] = {"t": "active", "id": session_id}
    if name is not None:
        payload["name"] = name
    return json.dumps(payload)


def history(
    session_id: str, items: list[dict[str, Any]], ok: bool = True,
) -> str:
    return json.dumps(
        {"t": "history", "id": session_id, "items": items, "ok": ok},
    )


def transcript(text: str) -> str:
    return json.dumps({"t": "transcript", "text": text})


def error(message: str) -> str:
    return json.dumps({"t": "error", "msg": message})


# ---- Frame parser ----------------------------------------------------------


def parse_client(raw: str | bytes) -> dict[str, Any]:
    """Parse an inbound JSON frame. Raises ValueError on malformed JSON.

    Use this for text-mode frames only — binary PCM frames are consumed
    directly by the server's audio-capture path.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MalformedJSONFrame(e) from e
    if not isinstance(obj, dict) or "t" not in obj:
        raise MissingTField
    return obj


# ---- Constants exported for the TS generator -------------------------------

INBOUND_TYPES = (
    "hello",
    "text",
    "audio.start",
    "audio.stop",
    "sessions.list",
    "sessions.switch",
    "sessions.new",
    "stop",
)

OUTBOUND_TYPES = (
    "hello.ok",
    "assistant.delta",
    "assistant",
    "tool.start",
    "tool.end",
    "turn.done",
    "sessions",
    "active",
    "history",
    "transcript",
    "error",
)
