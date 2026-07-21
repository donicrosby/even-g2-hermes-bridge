"""Parakeet ASR via Swift sidecar (macOS Apple Neural Engine).

Only used when EVEN_G2_ASR_SIDECAR_BIN is set and the binary exists. Not
the primary path for the user's Linux deployment — kept for parity with
huntsyea's plugin and future macOS deployments.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from byoa_plugin.asr import (
    ENV_SIDECAR_BIN,
    ASRConfigMissingError,
    ASRResourceMissingError,
    ASRSidecarError,
)

LOG = logging.getLogger("byoa_plugin.asr.parakeet")

_SIDECAR_CLOSED = "closed unexpectedly"
_SIDECAR_NON_STRING_TEXT = "returned non-string text"
_FRAME_LENGTH_BYTES = 4


class ParakeetBackend:
    """Spawns the parakeet Swift sidecar; communicates via stdin/stdout JSON."""

    def __init__(self, *, sidecar_bin: str) -> None:
        """Initialize the backend with the sidecar binary path."""
        if not sidecar_bin:
            raise ASRConfigMissingError(ENV_SIDECAR_BIN)
        if not Path(sidecar_bin).exists():
            raise ASRResourceMissingError(sidecar_bin)
        self.sidecar_bin = sidecar_bin
        self._proc: subprocess.Popen | None = None

    def _ensure_started(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(  # noqa: S603  # trusted binary
                [self.sidecar_bin],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            detail = f"failed to start: {e}"
            raise ASRSidecarError(detail) from e
        return self._proc

    def transcribe(self, pcm16_bytes: bytes) -> str:
        """Transcribe PCM16 bytes through the parakeet sidecar."""
        proc = self._ensure_started()
        # Frame: [4-byte big-endian length][pcm bytes]
        # Expect response: [4-byte big-endian length][json bytes]
        try:
            header = len(pcm16_bytes).to_bytes(_FRAME_LENGTH_BYTES, "big")
            assert proc.stdin is not None  # noqa: S101  # defensive check
            proc.stdin.write(header)
            proc.stdin.write(pcm16_bytes)
            proc.stdin.flush()

            assert proc.stdout is not None  # noqa: S101  # defensive check
            resp_header = proc.stdout.read(_FRAME_LENGTH_BYTES)
            if len(resp_header) != _FRAME_LENGTH_BYTES:
                raise ASRSidecarError(_SIDECAR_CLOSED)
            resp_len = int.from_bytes(resp_header, "big")
            resp_body = proc.stdout.read(resp_len)
            payload = json.loads(resp_body.decode("utf-8"))
            text = payload.get("text", "")
            if not isinstance(text, str):
                raise ASRSidecarError(_SIDECAR_NON_STRING_TEXT)
            LOG.debug("parakeet OK: text=%r", text[:80])
            return text.strip()
        except (OSError, ValueError, json.JSONDecodeError) as e:
            # Kill the sidecar so the next call restarts fresh.
            if self._proc is not None:
                self._proc.kill()
                self._proc = None
            raise ASRSidecarError(str(e)) from e
