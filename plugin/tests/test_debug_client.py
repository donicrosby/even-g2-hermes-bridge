"""Unit tests for the debug_client CLI's pure helpers.

Covers parse_send_spec (the --send TYPE[:ARG] parser) and format_summary
(the final stdout table formatter). The async run_client flow is exercised
end-to-end by plugin/tests/test_integration_ws.py against the real
BridgeServer fixture.
"""

from __future__ import annotations

from collections import Counter

import pytest

from byoa_plugin import protocol
from byoa_plugin.debug_client import format_summary, parse_send_spec


class TestParseSendSpecText:
    def test_text_with_content(self) -> None:
        serialized, label = parse_send_spec("text:hello world")
        assert label == "text"
        frame = protocol.parse_client(serialized)
        assert frame["t"] == "text"
        assert frame["text"] == "hello world"

    def test_text_empty_content_raises(self) -> None:
        with pytest.raises(ValueError, match="text: requires a content argument"):
            parse_send_spec("text:")

    def test_text_no_colon_raises(self) -> None:
        with pytest.raises(ValueError, match="text: requires a content argument"):
            parse_send_spec("text")

    def test_text_strips_whitespace(self) -> None:
        _, label = parse_send_spec("  text:   padded  ")
        assert label == "text"


class TestParseSendSpecSessions:
    def test_sessions_list_dot_form(self) -> None:
        serialized, label = parse_send_spec("sessions.list")
        assert label == "sessions.list"
        frame = protocol.parse_client(serialized)
        assert frame["t"] == "sessions.list"

    def test_sessions_list_underscore_form(self) -> None:
        _, label = parse_send_spec("sessions_list")
        assert label == "sessions.list"

    def test_sessions_switch_with_target(self) -> None:
        serialized, label = parse_send_spec("sessions.switch:+1")
        assert label == "sessions.switch"
        frame = protocol.parse_client(serialized)
        assert frame["t"] == "sessions.switch"
        assert frame["id"] == "+1"

    def test_sessions_switch_missing_target_raises(self) -> None:
        with pytest.raises(ValueError, match=r"sessions\.switch: requires a target"):
            parse_send_spec("sessions.switch:")

    def test_sessions_new(self) -> None:
        serialized, label = parse_send_spec("sessions.new")
        assert label == "sessions.new"
        frame = protocol.parse_client(serialized)
        assert frame["t"] == "sessions.new"


class TestParseSendSpecAudioAndStop:
    def test_audio_start(self) -> None:
        serialized, label = parse_send_spec("audio.start")
        assert label == "audio.start"
        frame = protocol.parse_client(serialized)
        assert frame["t"] == "audio.start"

    def test_audio_stop(self) -> None:
        _, label = parse_send_spec("audio.stop")
        assert label == "audio.stop"

    def test_audio_start_underscore_form(self) -> None:
        _, label = parse_send_spec("audio_start")
        assert label == "audio.start"

    def test_stop(self) -> None:
        serialized, label = parse_send_spec("stop")
        assert label == "stop"
        frame = protocol.parse_client(serialized)
        assert frame["t"] == "stop"


class TestParseSendSpecErrors:
    def test_unknown_frame_type_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown frame type"):
            parse_send_spec("frobnicate:foo")

    def test_empty_spec_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown frame type"):
            parse_send_spec("")

    def test_case_insensitive_head(self) -> None:
        _, label = parse_send_spec("TEXT:hello")
        assert label == "text"


class TestFormatSummary:
    def test_empty_counters_return_placeholder(self) -> None:
        result = format_summary(Counter(), Counter())
        assert result == "(no frames exchanged)"

    def test_sent_only(self) -> None:
        sent: Counter[str] = Counter({"hello": 1, "text": 3})
        result = format_summary(sent, Counter())
        assert "hello" in result
        assert "text" in result
        assert "1" in result
        assert "3" in result

    def test_received_only(self) -> None:
        received: Counter[str] = Counter({"hello.ok": 1, "assistant.delta": 5})
        result = format_summary(Counter(), received)
        assert "hello.ok" in result
        assert "assistant.delta" in result

    def test_both_directions(self) -> None:
        sent: Counter[str] = Counter({"hello": 1})
        received: Counter[str] = Counter({"hello.ok": 1, "assistant.delta": 2})
        result = format_summary(sent, received)
        assert "hello" in result
        assert "hello.ok" in result
        assert "assistant.delta" in result

    def test_column_alignment(self) -> None:
        sent: Counter[str] = Counter({"x": 1})
        received: Counter[str] = Counter({"long_frame_name": 2})
        result = format_summary(sent, received)
        lines = result.splitlines()
        assert len(lines) >= 3
        assert any("long_frame_name" in line for line in lines)
        assert any("x" in line for line in lines)

    def test_zero_sent_column_for_receive_only_kinds(self) -> None:
        sent: Counter[str] = Counter({"hello": 1})
        received: Counter[str] = Counter({"hello": 1, "error": 2})
        result = format_summary(sent, received)
        assert "error" in result
