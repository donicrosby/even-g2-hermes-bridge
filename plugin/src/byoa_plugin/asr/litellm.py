"""LiteLLM Whisper backend — POSTs PCM-WAV to LiteLLM's /v1/audio/transcriptions.

Preferred backend when EVEN_G2_ASR_LITELLM_MODEL is set. Routes through the
user's LiteLLM proxy to whatever Whisper-compatible model they've configured
(e.g. whisper on ROCm).
"""

from __future__ import annotations

import io
import logging
import wave

import httpx

from byoa_plugin.asr import (
    ENV_LITELLM_API_KEY,
    ENV_LITELLM_BASE_URL,
    ENV_LITELLM_MODEL,
    ASRConfigMissing,
    ASRHTTPError,
    ASRResponseError,
    ASRTransportError,
)

LOG = logging.getLogger("byoa_plugin.asr.litellm")

# Whisper expects 16kHz mono WAV. The glasses send PCM16 LE 16kHz mono,
# so we just wrap it as WAV.
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes per sample (16-bit)
CHANNELS = 1

# Network timeout for the LiteLLM call. Generous — Whisper on a cold GPU
# can take a few seconds.
TIMEOUT_SEC = 30.0


def pcm16_to_wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw PCM16 LE bytes as a WAV file in-memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class LiteLLMWhisperBackend:
    """Calls LiteLLM's /v1/audio/transcriptions endpoint."""

    def __init__(self, *, model: str, base_url: str, api_key: str) -> None:
        if not model:
            raise ASRConfigMissing(ENV_LITELLM_MODEL)
        if not base_url:
            raise ASRConfigMissing(ENV_LITELLM_BASE_URL)
        if not api_key:
            raise ASRConfigMissing(ENV_LITELLM_API_KEY)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def transcribe(self, pcm16_bytes: bytes) -> str:
        wav_bytes = pcm16_to_wav_bytes(pcm16_bytes)
        url = f"{self.base_url}/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model}

        try:
            with httpx.Client(timeout=TIMEOUT_SEC) as client:
                resp = client.post(url, headers=headers, files=files, data=data)
        except httpx.HTTPError as e:
            raise ASRTransportError(str(e)) from e

        if resp.status_code >= 400:
            body = resp.text[:200]
            raise ASRHTTPError(resp.status_code, body)

        try:
            payload = resp.json()
        except ValueError as e:
            detail = f"non-JSON: {e}"
            raise ASRResponseError(detail) from e

        text = payload.get("text", "")
        if not isinstance(text, str):
            detail = f"non-string text field: {type(text).__name__}"
            raise ASRResponseError(detail)

        LOG.debug(
            "litellm whisper OK: pcm_in=%d bytes text=%r",
            len(pcm16_bytes),
            text[:80],
        )
        return text.strip()
