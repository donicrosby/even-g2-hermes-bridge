"""Tests for the ASR dispatcher fallback chain.

Covers task 10.6 from openspec/changes/build-even-g2-hermes-platform/tasks.md:
  - parakeet sidecar binary path is invalid → ASRResourceMissing
  - dispatcher falls through to whisper-tiny CPU fallback

The whisper fallback is mocked so we don't pull faster-whisper/torch into
the test environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from byoa_plugin.asr import transcribe

if TYPE_CHECKING:
    from pathlib import Path


def _nonsilent_pcm(num_samples: int = 200) -> bytes:
    """Build PCM16 LE bytes with nonzero samples so _is_silent returns False."""
    return b"\x01\x00" * num_samples


class TestParakeetFallback:
    """Task 10.6: invalid sidecar path → whisper-tiny fallback."""

    async def test_invalid_sidecar_bin_falls_through_to_whisper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        reset_env: dict[str, str],
        hermes_home: Path,
    ) -> None:
        from byoa_plugin.config import BridgeConfig

        cfg = BridgeConfig(asr_sidecar_bin="/nonexistent/parakeet-bin")

        captured: list[bytes] = []

        class StubWhisperBackend:
            def transcribe(self, pcm16_bytes: bytes) -> str:
                captured.append(pcm16_bytes)
                return "stub transcription"

        monkeypatch.setattr(
            "byoa_plugin.asr.whisper_fallback.WhisperCPUBackend",
            StubWhisperBackend,
        )

        result = transcribe(_nonsilent_pcm(), cfg)

        assert result == "stub transcription"
        assert len(captured) == 1

    async def test_invalid_sidecar_does_not_call_litellm_when_unconfigured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        reset_env: dict[str, str],
        hermes_home: Path,
    ) -> None:
        from byoa_plugin.config import BridgeConfig

        cfg = BridgeConfig(
            asr_sidecar_bin="/nonexistent/parakeet-bin",
            asr_litellm_model="",  # skip litellm
        )

        litellm_calls: list[tuple[str, str, str]] = []

        class StubLiteLLMBackend:
            def __init__(self, *, model: str, base_url: str, api_key: str) -> None:
                litellm_calls.append((model, base_url, api_key))

            def transcribe(self, pcm16_bytes: bytes) -> str:
                return "should-not-be-called"

        monkeypatch.setattr(
            "byoa_plugin.asr.litellm.LiteLLMWhisperBackend",
            StubLiteLLMBackend,
        )

        class StubWhisper:
            def transcribe(self, _pcm: bytes) -> str:
                return "whisper-stub"

        monkeypatch.setattr(
            "byoa_plugin.asr.whisper_fallback.WhisperCPUBackend",
            StubWhisper,
        )

        transcribe(_nonsilent_pcm(), cfg)

        assert litellm_calls == []


class TestEmptyAndSilentAudioShortCircuit:
    """Task 7.6: empty/silent audio returns empty string without invoking backends."""

    def test_empty_pcm_returns_empty(
        self,
        reset_env: dict[str, str],
        hermes_home: Path,
    ) -> None:
        from byoa_plugin.config import BridgeConfig

        cfg = BridgeConfig()
        result = transcribe(b"", cfg)
        assert result == ""

    def test_all_zero_pcm_returns_empty(
        self,
        reset_env: dict[str, str],
        hermes_home: Path,
    ) -> None:
        from byoa_plugin.config import BridgeConfig

        cfg = BridgeConfig()
        result = transcribe(b"\x00" * 4096, cfg)
        assert result == ""

    def test_very_short_pcm_treated_as_silent(
        self,
        reset_env: dict[str, str],
        hermes_home: Path,
    ) -> None:
        from byoa_plugin.config import BridgeConfig

        cfg = BridgeConfig()
        result = transcribe(b"\x01\x00", cfg)
        assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
