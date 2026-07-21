"""faster-whisper CPU fallback for voice ASR.

Always available. Lazy-loads the whisper-tiny model on first use, then
reuses it across all subsequent transcription calls within the process.

This is the slowest backend (~2-5s per utterance on CPU) but requires no
external services and works on any host.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from byoa_plugin.asr import ASRModelLoadError, ASRTranscribeError

LOG = logging.getLogger("byoa_plugin.asr.whisper")

# Hardcoded for v1 — could be configurable later if a larger model helps.
WHISPER_MODEL_NAME = "whisper-tiny"

_WHISPER_NOT_INSTALLED_HINT = "faster-whisper not installed; run `uv sync` in plugin/"

# Lock guards the lazy model load (the first call downloads weights).
_LOAD_LOCK = threading.Lock()
_LOADED_MODEL = None


def _load_model() -> object:
    """Lazy-load the faster-whisper model. Thread-safe."""
    global _LOADED_MODEL  # noqa: PLW0603  # lazy singleton
    if _LOADED_MODEL is not None:
        return _LOADED_MODEL
    with _LOAD_LOCK:
        if _LOADED_MODEL is not None:
            return _LOADED_MODEL
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as e:
            raise ASRModelLoadError(_WHISPER_NOT_INSTALLED_HINT) from e
        LOG.info("loading %s (first call may download weights)...", WHISPER_MODEL_NAME)
        try:
            _LOADED_MODEL = WhisperModel(
                WHISPER_MODEL_NAME,
                device="cpu",
                compute_type="int8",
            )
        except Exception as e:
            detail = f"failed to load {WHISPER_MODEL_NAME}: {e}"
            raise ASRModelLoadError(detail) from e
        LOG.info("%s loaded", WHISPER_MODEL_NAME)
        return _LOADED_MODEL


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    """Convert raw PCM16 LE bytes to a float32 array normalized to [-1, 1]."""
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    return arr / 32768.0


class WhisperCPUBackend:
    """Transcribes via faster-whisper on CPU."""

    def transcribe(self, pcm16_bytes: bytes) -> str:
        """Transcribe PCM16 bytes through faster-whisper."""
        model = _load_model()
        audio = pcm16_to_float32(pcm16_bytes)
        try:
            segments, _info = model.transcribe(audio, language=None, vad_filter=True)
            text = " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            raise ASRTranscribeError(str(e)) from e
        LOG.debug("whisper OK: pcm_in=%d text=%r", len(pcm16_bytes), text[:80])
        return text
