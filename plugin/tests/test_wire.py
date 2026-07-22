"""Round-trip tests for wire.py frame constructors and parse_frame.

Replaces the former test_protocol.py (which locked in the JSON wire format).
These tests lock in the Protobuf wire format: every constructor produces
bytes that parse back to a Frame with the expected oneof variant and field
values. parse_frame is also covered for error paths.
"""

from __future__ import annotations

import pytest

from byoa_plugin import wire

# ===== parse_frame round-trips for inbound constructors (client → server) =====


class TestInboundConstructors:
    def test_hello_includes_token_and_device(self) -> None:
        frame = wire.parse_frame(wire.hello("tok123", "g2-serial"))
        assert frame.WhichOneof("payload") == "hello"
        assert frame.hello.token == "tok123"  # noqa: S105
        assert frame.hello.device == "g2-serial"

    def test_hello_defaults_device_to_g2(self) -> None:
        frame = wire.parse_frame(wire.hello("tok123"))
        assert frame.hello.device == "g2"

    def test_text_carries_content(self) -> None:
        frame = wire.parse_frame(wire.text("hello world"))
        assert frame.WhichOneof("payload") == "text"
        assert frame.text.content == "hello world"

    def test_text_preserves_utf8(self) -> None:
        frame = wire.parse_frame(wire.text("héllo wörld 🎉"))
        assert frame.text.content == "héllo wörld 🎉"

    def test_audio_start_has_no_payload_fields(self) -> None:
        frame = wire.parse_frame(wire.audio_start())
        assert frame.WhichOneof("payload") == "audio_start"

    def test_audio_stop_has_no_payload_fields(self) -> None:
        frame = wire.parse_frame(wire.audio_stop())
        assert frame.WhichOneof("payload") == "audio_stop"

    def test_audio_data_carries_pcm_bytes(self) -> None:
        pcm = bytes(range(256))
        frame = wire.parse_frame(wire.audio_data(pcm))
        assert frame.WhichOneof("payload") == "audio_data"
        assert frame.audio_data.pcm == pcm

    def test_sessions_list_has_no_payload_fields(self) -> None:
        frame = wire.parse_frame(wire.sessions_list())
        assert frame.WhichOneof("payload") == "sessions_list"

    def test_sessions_switch_carries_target(self) -> None:
        frame = wire.parse_frame(wire.sessions_switch("+1"))
        assert frame.WhichOneof("payload") == "sessions_switch"
        assert frame.sessions_switch.target == "+1"

    def test_sessions_new_has_no_payload_fields(self) -> None:
        frame = wire.parse_frame(wire.sessions_new())
        assert frame.WhichOneof("payload") == "sessions_new"

    def test_stop_has_no_payload_fields(self) -> None:
        frame = wire.parse_frame(wire.stop())
        assert frame.WhichOneof("payload") == "stop"


# ===== parse_frame round-trips for outbound constructors (server → client) =====


class TestOutboundConstructors:
    def test_hello_ok_minimal(self) -> None:
        frame = wire.parse_frame(wire.hello_ok())
        assert frame.WhichOneof("payload") == "hello_ok"
        assert not frame.hello_ok.HasField("active")
        assert list(frame.hello_ok.caps) == []

    def test_hello_ok_with_active(self) -> None:
        frame = wire.parse_frame(wire.hello_ok(active="sess-1"))
        assert frame.hello_ok.active == "sess-1"

    def test_hello_ok_with_caps(self) -> None:
        frame = wire.parse_frame(wire.hello_ok(caps=["streaming", "sessions"]))
        assert list(frame.hello_ok.caps) == ["streaming", "sessions"]

    def test_assistant_delta_carries_text(self) -> None:
        frame = wire.parse_frame(wire.assistant_delta("Hello "))
        assert frame.WhichOneof("payload") == "assistant_delta"
        assert frame.assistant_delta.text == "Hello "

    def test_assistant_full_carries_text(self) -> None:
        frame = wire.parse_frame(wire.assistant_full("Hello world"))
        assert frame.WhichOneof("payload") == "assistant"
        assert frame.assistant.text == "Hello world"

    def test_tool_start_minimal(self) -> None:
        frame = wire.parse_frame(wire.tool_start("web_search"))
        assert frame.WhichOneof("payload") == "tool_start"
        assert frame.tool_start.name == "web_search"
        assert not frame.tool_start.HasField("label")
        assert not frame.tool_start.HasField("emoji")

    def test_tool_start_with_label_and_emoji(self) -> None:
        frame = wire.parse_frame(
            wire.tool_start("web_search", label="Searching the web", emoji="🔍"),
        )
        assert frame.tool_start.label == "Searching the web"
        assert frame.tool_start.emoji == "🔍"

    def test_tool_end_defaults_ok_true(self) -> None:
        frame = wire.parse_frame(wire.tool_end("web_search"))
        assert frame.WhichOneof("payload") == "tool_end"
        assert frame.tool_end.name == "web_search"
        assert frame.tool_end.ok is True

    def test_tool_end_with_ok_false(self) -> None:
        frame = wire.parse_frame(wire.tool_end("web_search", ok=False))
        assert frame.tool_end.ok is False

    def test_turn_done_has_no_payload_fields(self) -> None:
        frame = wire.parse_frame(wire.turn_done())
        assert frame.WhichOneof("payload") == "turn_done"

    def test_sessions_minimal(self) -> None:
        frame = wire.parse_frame(wire.sessions(items=[]))
        assert frame.WhichOneof("payload") == "sessions"
        assert list(frame.sessions.items) == []
        assert not frame.sessions.HasField("active")

    def test_sessions_with_items_and_active(self) -> None:
        items = [
            {"id": "s1", "name": "First"},
            {"id": "s2", "name": "Second"},
        ]
        frame = wire.parse_frame(wire.sessions(items=items, active="s2"))
        assert len(frame.sessions.items) == 2
        assert frame.sessions.items[0].id == "s1"
        assert frame.sessions.items[0].name == "First"
        assert frame.sessions.items[1].id == "s2"
        assert frame.sessions.items[1].name == "Second"
        assert frame.sessions.active == "s2"

    def test_active_minimal(self) -> None:
        frame = wire.parse_frame(wire.active("s1"))
        assert frame.WhichOneof("payload") == "active"
        assert frame.active.id == "s1"
        assert not frame.active.HasField("name")

    def test_active_with_name(self) -> None:
        frame = wire.parse_frame(wire.active("s1", name="First chat"))
        assert frame.active.name == "First chat"

    def test_history_minimal(self) -> None:
        frame = wire.parse_frame(wire.history("s1", items=[]))
        assert frame.WhichOneof("payload") == "history"
        assert frame.history.id == "s1"
        assert frame.history.ok is True

    def test_history_with_items_and_ok_false(self) -> None:
        items = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        frame = wire.parse_frame(wire.history("s1", items=items, ok=False))
        assert len(frame.history.items) == 2
        assert frame.history.items[0].role == "user"
        assert frame.history.items[0].content == "hi"
        assert frame.history.items[1].role == "assistant"
        assert frame.history.items[1].content == "hello"
        assert frame.history.ok is False

    def test_transcript_carries_text(self) -> None:
        frame = wire.parse_frame(wire.transcript("the user said this"))
        assert frame.WhichOneof("payload") == "transcript"
        assert frame.transcript.text == "the user said this"

    def test_error_carries_message(self) -> None:
        frame = wire.parse_frame(wire.error("something broke"))
        assert frame.WhichOneof("payload") == "error"
        assert frame.error.msg == "something broke"


# ===== parse_frame error paths =====


class TestParseFrameErrors:
    def test_empty_bytes_produce_frame_with_no_payload(self) -> None:
        frame = wire.parse_frame(b"")
        assert frame.WhichOneof("payload") is None

    def test_raises_frame_parse_error_on_random_bytes(self) -> None:
        with pytest.raises(wire.FrameParseError):
            wire.parse_frame(b"\x00\x01\x02\x03 not a frame")

    def test_raises_frame_parse_error_on_truncated_frame(self) -> None:
        valid = wire.text("hello")
        truncated = valid[: len(valid) // 2]
        with pytest.raises(wire.FrameParseError):
            wire.parse_frame(truncated)

    def test_frame_parse_error_is_value_error_subclass(self) -> None:
        # Ensures callers that catch ValueError still work.
        assert issubclass(wire.FrameParseError, ValueError)


# ===== Wire invariants =====


class TestWireInvariants:
    def test_every_constructor_returns_bytes(self) -> None:
        # Static type-check enforces this, but assert at runtime too so a
        # future refactor that swaps return types is caught immediately.
        constructors = [
            wire.hello("t"),
            wire.text("t"),
            wire.audio_start(),
            wire.audio_stop(),
            wire.sessions_list(),
            wire.sessions_switch("+1"),
            wire.sessions_new(),
            wire.stop(),
            wire.audio_data(b"\x00\x01"),
            wire.hello_ok(),
            wire.assistant_delta("t"),
            wire.assistant_full("t"),
            wire.tool_start("n"),
            wire.tool_end("n"),
            wire.turn_done(),
            wire.sessions(items=[]),
            wire.active("id"),
            wire.history("id", items=[]),
            wire.transcript("t"),
            wire.error("t"),
        ]
        for serialized in constructors:
            assert isinstance(serialized, bytes), (
                f"Constructor returned {type(serialized).__name__}, expected bytes"
            )

    def test_round_trip_preserves_frame_type_discriminator(self) -> None:
        # Pairs of (constructor call, expected oneof kind) for a representative
        # sample. Catches accidental cross-wiring of payload variants.
        samples: list[tuple[bytes, str]] = [
            (wire.hello("t"), "hello"),
            (wire.text("t"), "text"),
            (wire.audio_start(), "audio_start"),
            (wire.audio_stop(), "audio_stop"),
            (wire.audio_data(b"\x00"), "audio_data"),
            (wire.sessions_list(), "sessions_list"),
            (wire.sessions_switch("+1"), "sessions_switch"),
            (wire.sessions_new(), "sessions_new"),
            (wire.stop(), "stop"),
            (wire.hello_ok(), "hello_ok"),
            (wire.assistant_delta("t"), "assistant_delta"),
            (wire.assistant_full("t"), "assistant"),
            (wire.tool_start("n"), "tool_start"),
            (wire.tool_end("n"), "tool_end"),
            (wire.turn_done(), "turn_done"),
            (wire.sessions(items=[]), "sessions"),
            (wire.active("id"), "active"),
            (wire.history("id", items=[]), "history"),
            (wire.transcript("t"), "transcript"),
            (wire.error("t"), "error"),
        ]
        for serialized, expected_kind in samples:
            frame = wire.parse_frame(serialized)
            assert frame.WhichOneof("payload") == expected_kind, (
                f"Expected payload kind {expected_kind!r} but got "
                f"{frame.WhichOneof('payload')!r}"
            )
