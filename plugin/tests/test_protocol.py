"""Characterization tests for protocol.py frame constructors and parser.

Locks the wire format before refactoring so behavior is preserved.
"""

from __future__ import annotations

import json

import pytest

from byoa_plugin import protocol as proto


class TestParseClient:
    def test_parses_valid_hello_frame_from_str(self) -> None:
        result = proto.parse_client('{"t":"hello","token":"abc","device":"g2"}')
        assert result == {"t": "hello", "token": "abc", "device": "g2"}

    def test_parses_valid_frame_from_bytes(self) -> None:
        result = proto.parse_client(b'{"t":"text","text":"hi"}')
        assert result == {"t": "text", "text": "hi"}

    def test_raises_on_malformed_json(self) -> None:
        with pytest.raises(ValueError, match="malformed JSON"):
            proto.parse_client("not json{")

    def test_raises_on_missing_t_field(self) -> None:
        with pytest.raises(ValueError, match="missing required 't'"):
            proto.parse_client('{"foo":"bar"}')

    def test_raises_on_non_dict_json(self) -> None:
        with pytest.raises(ValueError, match="missing required 't'"):
            proto.parse_client('["t","hello"]')

    def test_handles_utf8_content(self) -> None:
        result = proto.parse_client('{"t":"text","text":"héllo wörld"}')
        assert result["text"] == "héllo wörld"


class TestInboundConstructors:
    def test_hello_includes_token_and_device(self) -> None:
        frame = json.loads(proto.hello("tok123", "g2-serial-xyz"))
        assert frame == {"t": "hello", "token": "tok123", "device": "g2-serial-xyz"}

    def test_hello_defaults_device_to_g2(self) -> None:
        frame = json.loads(proto.hello("tok123"))
        assert frame["device"] == "g2"

    def test_text_carries_content(self) -> None:
        frame = json.loads(proto.text("what time is it"))
        assert frame == {"t": "text", "text": "what time is it"}

    def test_audio_start_has_no_payload(self) -> None:
        frame = json.loads(proto.audio_start())
        assert frame == {"t": "audio.start"}

    def test_audio_stop_has_no_payload(self) -> None:
        frame = json.loads(proto.audio_stop())
        assert frame == {"t": "audio.stop"}

    def test_sessions_switch_carries_id(self) -> None:
        frame = json.loads(proto.sessions_switch("+1"))
        assert frame == {"t": "sessions.switch", "id": "+1"}

    def test_sessions_list_has_no_payload(self) -> None:
        frame = json.loads(proto.sessions_list())
        assert frame == {"t": "sessions.list"}

    def test_sessions_new_has_no_payload(self) -> None:
        frame = json.loads(proto.sessions_new())
        assert frame == {"t": "sessions.new"}

    def test_stop_has_no_payload(self) -> None:
        frame = json.loads(proto.stop())
        assert frame == {"t": "stop"}


class TestOutboundConstructors:
    def test_hello_ok_with_active_and_caps(self) -> None:
        frame = json.loads(proto.hello_ok(active="sess-1", caps=["text", "voice"]))
        assert frame == {
            "t": "hello.ok",
            "active": "sess-1",
            "caps": ["text", "voice"],
        }

    def test_hello_ok_minimal(self) -> None:
        frame = json.loads(proto.hello_ok())
        assert frame == {"t": "hello.ok"}

    def test_assistant_delta_carries_text(self) -> None:
        frame = json.loads(proto.assistant_delta("Hello"))
        assert frame == {"t": "assistant.delta", "text": "Hello"}

    def test_assistant_full_carries_text(self) -> None:
        frame = json.loads(proto.assistant_full("Full reply"))
        assert frame == {"t": "assistant", "text": "Full reply"}

    def test_tool_start_minimal(self) -> None:
        frame = json.loads(proto.tool_start("web_search"))
        assert frame == {"t": "tool.start", "name": "web_search"}

    def test_tool_start_with_label_and_emoji(self) -> None:
        frame = json.loads(
            proto.tool_start("web_search", label="Searching", emoji="🔍"),
        )
        assert frame == {
            "t": "tool.start",
            "name": "web_search",
            "label": "Searching",
            "emoji": "🔍",
        }

    def test_tool_end_carries_name_and_ok(self) -> None:
        frame = json.loads(proto.tool_end("web_search", ok=True))
        assert frame == {"t": "tool.end", "name": "web_search", "ok": True}

    def test_turn_done_has_no_payload(self) -> None:
        frame = json.loads(proto.turn_done())
        assert frame == {"t": "turn.done"}

    def test_sessions_carries_items_and_active(self) -> None:
        items = [{"id": "s1", "name": "Chat 1"}]
        frame = json.loads(proto.sessions(items, active="s1"))
        assert frame == {"t": "sessions", "items": items, "active": "s1"}

    def test_active_carries_id_and_name(self) -> None:
        frame = json.loads(proto.active("s1", name="Chat 1"))
        assert frame == {"t": "active", "id": "s1", "name": "Chat 1"}

    def test_active_without_name(self) -> None:
        frame = json.loads(proto.active("s1"))
        assert frame == {"t": "active", "id": "s1"}

    def test_history_carries_session_and_items(self) -> None:
        items = [{"role": "user", "content": "hi"}]
        frame = json.loads(proto.history("s1", items, ok=True))
        assert frame == {
            "t": "history",
            "id": "s1",
            "items": items,
            "ok": True,
        }

    def test_transcript_carries_text(self) -> None:
        frame = json.loads(proto.transcript("hello world"))
        assert frame == {"t": "transcript", "text": "hello world"}

    def test_error_carries_message(self) -> None:
        frame = json.loads(proto.error("something broke"))
        assert frame == {"t": "error", "msg": "something broke"}


class TestConstants:
    def test_streaming_cursor_is_trailing_block_space(self) -> None:
        assert proto.STREAMING_CURSOR == " ▉"

    def test_inbound_types_count(self) -> None:
        assert len(proto.INBOUND_TYPES) == 8

    def test_outbound_types_count(self) -> None:
        assert len(proto.OUTBOUND_TYPES) == 11

    def test_inbound_types_include_all_frame_families(self) -> None:
        assert set(proto.INBOUND_TYPES) == {
            "hello",
            "text",
            "audio.start",
            "audio.stop",
            "sessions.list",
            "sessions.switch",
            "sessions.new",
            "stop",
        }

    def test_outbound_types_include_all_frame_families(self) -> None:
        assert set(proto.OUTBOUND_TYPES) == {
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
        }
