"""Voice ASR for the even_g2 plugin.

Three backends, tried in order:
  1. LiteLLM Whisper (preferred when EVEN_G2_ASR_LITELLM_MODEL is set) —
     POSTs WAV bytes to {base}/v1/audio/transcriptions
  2. Parakeet via Swift sidecar (only on macOS, future path)
  3. faster-whisper CPU fallback (always available)

All backends implement: `transcribe(pcm16_bytes: bytes) -> str`
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from byoa_plugin.config import BridgeConfig

LOG = logging.getLogger("byoa_plugin.asr")

# Env var name constants — imported by backends to avoid string literals at raise sites.
ENV_LITELLM_MODEL = "EVEN_G2_ASR_LITELLM_MODEL"
ENV_LITELLM_BASE_URL = "EVEN_G2_ASR_LITELLM_BASE_URL"
ENV_LITELLM_API_KEY = "EVEN_G2_ASR_LITELLM_API_KEY"
ENV_SIDECAR_BIN = "EVEN_G2_ASR_SIDECAR_BIN"


class ASRUnavailable(Exception):
    """Base for ASR backend failures.

    Backends raise a specific subclass so callers can distinguish failure
    modes (config, transport, HTTP, response, sidecar, model load,
    transcribe) without parsing message strings.
    """


class ASRConfigMissing(ASRUnavailable):
    """A required env var / config field is empty."""

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(f"{env_var} not set")


class ASRResourceMissing(ASRUnavailable):
    """A required file/binary path does not exist."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"required resource not found at {path!r}")


class ASRTransportError(ASRUnavailable):
    """Network/transport failure reaching the ASR service."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"transport error: {detail}")


class ASRHTTPError(ASRUnavailable):
    """ASR service returned an HTTP error response."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


class ASRResponseError(ASRUnavailable):
    """ASR service returned a malformed/unexpected response."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"malformed response: {detail}")


class ASRSidecarError(ASRUnavailable):
    """Subprocess sidecar failed or returned bad data."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"sidecar error: {detail}")


class ASRModelLoadError(ASRUnavailable):
    """Backend couldn't load its model (deps missing, weights fail)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"model load failed: {detail}")


class ASRTranscribeError(ASRUnavailable):
    """Backend loaded but transcribe() crashed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"transcribe failed: {detail}")


def transcribe(pcm16_bytes: bytes, cfg: BridgeConfig) -> str:
    """Transcribe PCM16 16kHz mono bytes to text.

    Tries LiteLLM first (if configured), then parakeet (if available),
    then faster-whisper CPU fallback. Returns "" for empty/silent audio.

    Raises ASRUnavailable if ALL backends fail.
    """
    if not pcm16_bytes:
        return ""

    # Quick silence/DC check — if all bytes are zero, skip ASR entirely.
    if _is_silent(pcm16_bytes):
        LOG.debug("audio is silent, skipping ASR")
        return ""

    # Try LiteLLM Whisper first
    if cfg.asr_litellm_model:
        try:
            from byoa_plugin.asr.litellm import LiteLLMWhisperBackend  # noqa: PLC0415

            backend = LiteLLMWhisperBackend(
                model=cfg.asr_litellm_model,
                base_url=cfg.asr_litellm_base_url,
                api_key=cfg.asr_litellm_api_key,
            )
            return backend.transcribe(pcm16_bytes)
        except ASRUnavailable as e:
            LOG.warning("LiteLLM ASR unavailable, falling back: %s", e)
        except Exception as e:
            LOG.exception("LiteLLM ASR crashed, falling back: %s", e)

    # Try parakeet sidecar (macOS only)
    if cfg.asr_sidecar_bin:
        try:
            from byoa_plugin.asr.parakeet import ParakeetBackend  # noqa: PLC0415

            backend = ParakeetBackend(sidecar_bin=cfg.asr_sidecar_bin)
            return backend.transcribe(pcm16_bytes)
        except ASRUnavailable as e:
            LOG.warning("parakeet ASR unavailable, falling back: %s", e)
        except Exception as e:
            LOG.exception("parakeet ASR crashed, falling back: %s", e)

    # Final fallback: faster-whisper CPU
    from byoa_plugin.asr.whisper_fallback import WhisperCPUBackend  # noqa: PLC0415

    backend = WhisperCPUBackend()
    return backend.transcribe(pcm16_bytes)


def _is_silent(pcm16_bytes: bytes) -> bool:
    """Quick check: is the audio entirely zero bytes?

    This is a fast-path for "user held the button but didn't speak."
    A more sophisticated VAD would be nicer but adds complexity.
    """
    # Sample every 1024th byte for speed (still catches all-zero audio).
    if len(pcm16_bytes) < 64:
        return True
    for i in range(0, len(pcm16_bytes), 2048):
        chunk = pcm16_bytes[i : i + 2048]
        if any(chunk):
            return False
    return True
