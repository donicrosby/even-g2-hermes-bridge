"""Parakeet ASR via Swift sidecar (macOS Apple Neural Engine).

Only used when EVEN_G2_ASR_SIDECAR_BIN is set and the binary exists. Not
the primary path for the user's Linux deployment — kept for parity with
huntsyea's plugin and future macOS deployments.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from byoa_plugin.asr import (
    ENV_SIDECAR_BIN,
    ASRConfigMissing,
    ASRResourceMissing,
    ASRSidecarError,
)

LOG = logging.getLogger("byoa_plugin.asr.parakeet")

_SIDECAR_CLOSED = "closed unexpectedly"
_SIDECAR_NON_STRING_TEXT = "returned non-string text"


class ParakeetBackend:
    """Spawns the parakeet Swift sidecar; communicates via stdin/stdout JSON."""

    def __init__(self, *, sidecar_bin: str) -> None:
        if not sidecar_bin:
            raise ASRConfigMissing(ENV_SIDECAR_BIN)
        if not os.path.exists(sidecar_bin):
            raise ASRResourceMissing(sidecar_bin)
        self.sidecar_bin = sidecar_bin
        self._proc: subprocess.Popen | None = None

    def _ensure_started(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(
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
        proc = self._ensure_started()
        # Frame: [4-byte big-endian length][pcm bytes]
        # Expect response: [4-byte big-endian length][json bytes]
        try:
            header = len(pcm16_bytes).to_bytes(4, "big")
            assert proc.stdin is not None
            proc.stdin.write(header)
            proc.stdin.write(pcm16_bytes)
            proc.stdin.flush()

            assert proc.stdout is not None
            resp_header = proc.stdout.read(4)
            if len(resp_header) != 4:
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
