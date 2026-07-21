"""Characterization tests for qr_setup payload builder and ASR silence check.

Pure functions tested here:
- qr_setup.build_payload: URL + token → query-string URL
- asr._is_silent: PCM bytes → bool (fast-path silence detection)
"""

from __future__ import annotations

from byoa_plugin.asr import _is_silent
from byoa_plugin.qr_setup import build_payload


class TestBuildPayload:
    def test_simple_url_gets_token_query_param(self) -> None:
        assert build_payload("wss://example.com:8443", "tok123") == (
            "wss://example.com:8443?token=tok123"
        )

    def test_url_with_existing_query_appends_token(self) -> None:
        result = build_payload("wss://example.com:8443/path?q=1", "tok123")
        assert result == "wss://example.com:8443/path?q=1&token=tok123"

    def test_url_with_multiple_existing_params(self) -> None:
        result = build_payload("wss://example.com?a=1&b=2", "tok")
        assert result == "wss://example.com?a=1&b=2&token=tok"

    def test_token_with_special_chars_not_url_encoded(self) -> None:
        # build_payload does not URL-encode; the glasses-app is expected to
        # handle the raw token. Lock this behavior.
        result = build_payload("wss://x", "abc/def+ghi=")
        assert result == "wss://x?token=abc/def+ghi="


class TestIsSilent:
    def test_empty_bytes_is_silent(self) -> None:
        assert _is_silent(b"") is True

    def test_short_bytes_under_64_is_silent(self) -> None:
        assert _is_silent(b"\x00" * 32) is True

    def test_all_zeros_is_silent(self) -> None:
        assert _is_silent(b"\x00" * 8000) is True

    def test_nonzero_bytes_is_not_silent(self) -> None:
        assert _is_silent(b"\x00\x01" * 100) is False

    def test_silent_then_nonzero_in_middle_is_not_silent(self) -> None:
        # 8KB of zeros followed by nonzero data
        data = b"\x00" * 8192 + b"\x01\x02" * 100
        assert _is_silent(data) is False

    def test_only_first_chunk_sampled_for_speed(self) -> None:
        # The function samples every 2048th byte for speed. A single nonzero
        # byte at an unsampled offset may be missed. This locks the behavior
        # so refactors know the trade-off.
        # 8KB of zeros with a single nonzero byte at offset 1 (sampled).
        data = bytearray(8192)
        data[1] = 0xFF  # offset 1 is in first chunk (0:2048)
        assert _is_silent(bytes(data)) is False
