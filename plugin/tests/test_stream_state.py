"""Characterization tests for StreamState.delta_for.

These are the load-bearing tests — delta computation is the core of the
streaming protocol. Any refactor of StreamState must preserve these
behaviors exactly.
"""

from __future__ import annotations

from byoa_plugin.connections import StreamState


class TestStreamStateDeltaFor:
    def test_first_call_returns_full_text(self) -> None:
        state = StreamState()
        assert state.delta_for("Hello") == "Hello"

    def test_subsequent_call_returns_only_suffix(self) -> None:
        state = StreamState()
        state.delta_for("Hello")
        assert state.delta_for("Hello world") == " world"

    def test_unchanged_text_returns_empty_delta(self) -> None:
        state = StreamState()
        state.delta_for("Hello")
        assert state.delta_for("Hello") == ""

    def test_strips_trailing_streaming_cursor_before_diffing(self) -> None:
        state = StreamState(sent_len=11)
        # "Hello world ▉" should be treated as "Hello world" (len 11)
        assert state.delta_for("Hello world ▉") == ""

    def test_cursor_stripped_during_progressive_stream(self) -> None:
        state = StreamState()
        state.delta_for("Hello")
        state.delta_for("Hello world ▉")
        # Next call without cursor, same content: empty delta
        assert state.delta_for("Hello world ▉") == ""

    def test_content_shrinks_resets_and_returns_full_text(self) -> None:
        state = StreamState(sent_len=10)
        # Content went from len-10 to len-2: treat as fresh
        result = state.delta_for("Hi")
        assert result == "Hi"
        assert state.sent_len == 2

    def test_reset_zeroes_sent_len(self) -> None:
        state = StreamState(sent_len=50)
        state.reset()
        assert state.sent_len == 0

    def test_reset_then_delta_returns_full_text(self) -> None:
        state = StreamState(sent_len=50)
        state.reset()
        assert state.delta_for("Fresh start") == "Fresh start"

    def test_progressive_stream_three_steps(self) -> None:
        state = StreamState()
        assert state.delta_for("The") == "The"
        assert state.delta_for("The quick") == " quick"
        assert state.delta_for("The quick brown fox") == " brown fox"

    def test_empty_string_after_content_resets(self) -> None:
        state = StreamState()
        state.delta_for("Some content")
        assert state.delta_for("") == ""

    def test_unicode_content(self) -> None:
        state = StreamState()
        assert state.delta_for("héllo") == "héllo"
        assert state.delta_for("héllo wörld") == " wörld"

    def test_multibyte_emoji(self) -> None:
        state = StreamState()
        state.delta_for("Hello 🌍")
        assert state.delta_for("Hello 🌍 World") == " World"
