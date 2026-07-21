"""Thin wrapper re-exporting frame constructor names from the generated Protobuf stubs.

Preserves the call-site names from the legacy ``protocol.py`` so the rest of the
plugin (server.py, adapter.py, hooks.py) only needs an import path change:
``from byoa_plugin import protocol as proto`` → ``from byoa_plugin import wire as proto``.

Constructors return ``bytes`` (serialized ``Frame``) instead of ``str`` (JSON).
Today this module is unused on the wire (the server still speaks JSON); it will
be wired in when the migration lands. Tests in ``tests/test_wire.py`` exercise
every constructor's round-trip so we can ship the wrapper ahead of the
migration with confidence.
"""

from __future__ import annotations

from typing import Any

from byoa_plugin.proto_gen import hermes_bridge_pb2 as _pb

# Frame envelope + parser exceptions
Frame = _pb.Frame


class FrameParseError(ValueError):
    """Raised when bytes cannot be parsed as a valid ``Frame`` Protobuf message."""


def parse_frame(raw: bytes) -> _pb.Frame:
    """Parse a serialized ``Frame``. Raises ``FrameParseError`` on malformed input."""
    frame = _pb.Frame()
    try:
        frame.ParseFromString(raw)
    except Exception as e:
        raise FrameParseError(str(e)) from e
    return frame


# ---- Inbound frame constructors (client → server) -------------------------


def hello(token: str, device: str = "g2") -> bytes:
    """Build a hello frame."""
    return _pb.Frame(hello=_pb.HelloFrame(token=token, device=device)).SerializeToString()


def text(content: str) -> bytes:
    """Build a text frame."""
    return _pb.Frame(text=_pb.TextFrame(content=content)).SerializeToString()


def audio_start() -> bytes:
    """Build an audio start frame."""
    return _pb.Frame(audio_start=_pb.AudioStartFrame()).SerializeToString()


def audio_stop() -> bytes:
    """Build an audio stop frame."""
    return _pb.Frame(audio_stop=_pb.AudioStopFrame()).SerializeToString()


def sessions_list() -> bytes:
    """Build a sessions list frame."""
    return _pb.Frame(sessions_list=_pb.SessionsListFrame()).SerializeToString()


def sessions_switch(target: str) -> bytes:
    """Target is either a session id or "+1"/"-1" for relative switching."""
    return _pb.Frame(
        sessions_switch=_pb.SessionsSwitchFrame(target=target),
    ).SerializeToString()


def sessions_new() -> bytes:
    """Build a sessions new frame."""
    return _pb.Frame(sessions_new=_pb.SessionsNewFrame()).SerializeToString()


def stop() -> bytes:
    """Build a stop frame."""
    return _pb.Frame(stop=_pb.StopFrame()).SerializeToString()


def audio_data(pcm: bytes) -> bytes:
    """Wrap raw PCM bytes in an AudioData frame.

    Used for transmission between audio.start and audio.stop.
    """
    return _pb.Frame(audio_data=_pb.AudioDataFrame(pcm=pcm)).SerializeToString()


# ---- Outbound frame constructors (server → client) ------------------------


def hello_ok(active: str | None = None, caps: list[str] | None = None) -> bytes:
    """Build a hello.ok frame."""
    kwargs: dict[str, Any] = {}
    if active is not None:
        kwargs["active"] = active
    if caps is not None:
        kwargs["caps"] = caps
    return _pb.Frame(hello_ok=_pb.HelloOkFrame(**kwargs)).SerializeToString()


def assistant_delta(text: str) -> bytes:
    """Build an assistant delta frame."""
    return _pb.Frame(
        assistant_delta=_pb.AssistantDeltaFrame(text=text),
    ).SerializeToString()


def assistant_full(text: str) -> bytes:
    """Build a full assistant frame."""
    return _pb.Frame(
        assistant=_pb.AssistantFullFrame(text=text),
    ).SerializeToString()


def tool_start(
    name: str, label: str | None = None, emoji: str | None = None,
) -> bytes:
    """Build a tool start frame."""
    kwargs: dict[str, Any] = {"name": name}
    if label:
        kwargs["label"] = label
    if emoji:
        kwargs["emoji"] = emoji
    return _pb.Frame(tool_start=_pb.ToolStartFrame(**kwargs)).SerializeToString()


def tool_end(name: str, *, ok: bool = True) -> bytes:
    """Build a tool end frame."""
    return _pb.Frame(
        tool_end=_pb.ToolEndFrame(name=name, ok=ok),
    ).SerializeToString()


def turn_done() -> bytes:
    """Build a turn done frame."""
    return _pb.Frame(turn_done=_pb.TurnDoneFrame()).SerializeToString()


def sessions(items: list[dict[str, Any]], active: str | None = None) -> bytes:
    """Build a sessions frame. ``items`` mirrors today's dict-of-str shape."""
    kwargs: dict[str, Any] = {
        "items": [
            _pb.SessionItem(
                id=str(i["id"]),
                name=str(i["name"]) if i.get("name") is not None else None,
            )
            for i in items
        ],
    }
    if active is not None:
        kwargs["active"] = active
    return _pb.Frame(sessions=_pb.SessionsFrame(**kwargs)).SerializeToString()


def active(session_id: str, name: str | None = None) -> bytes:
    """Build an active session frame."""
    kwargs: dict[str, Any] = {"id": session_id}
    if name is not None:
        kwargs["name"] = name
    return _pb.Frame(active=_pb.ActiveFrame(**kwargs)).SerializeToString()


def history(
    session_id: str,
    items: list[dict[str, Any]],
    *,
    ok: bool = True,
) -> bytes:
    """Build a history frame."""
    return _pb.Frame(
        history=_pb.HistoryFrame(
            id=session_id,
            items=[
                _pb.HistoryItem(
                    role=str(i.get("role", "")),
                    content=str(i.get("content", "")),
                )
                for i in items
            ],
            ok=ok,
        ),
    ).SerializeToString()


def transcript(text: str) -> bytes:
    """Build a transcript frame."""
    return _pb.Frame(
        transcript=_pb.TranscriptFrame(text=text),
    ).SerializeToString()


def error(message: str) -> bytes:
    """Build an error frame."""
    return _pb.Frame(error=_pb.ErrorFrame(msg=message)).SerializeToString()
