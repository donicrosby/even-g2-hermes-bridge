# voice-asr

## Purpose

Defines the three-backend voice transcription fallback chain for PCM16 audio: LiteLLM Whisper (preferred when EVEN_G2_ASR_LITELLM_MODEL is set, wraps PCM as WAV and POSTs to LiteLLM /v1/audio/transcriptions), parakeet-tdt on macOS via Apple Neural Engine (optional, via Swift sidecar), and faster-whisper whisper-tiny on local CPU (universal fallback). All backends lazy-load on first use to keep plugin startup fast.

## Requirements

### Requirement: Voice transcription from PCM16 audio

The plugin SHALL accept PCM16 16kHz mono audio bytes (accumulated between `audio.start` and `audio.stop` frames) and produce a text transcript via automatic speech recognition. The transcript SHALL be emitted as a `transcript` frame back to the glasses-app, and SHALL also be forwarded to the Hermes Gateway as a `MessageEvent` of type `voice`.

#### Scenario: Successful transcription

- **WHEN** the glasses-app sends `audio.start`, 2 seconds of PCM16 audio (saying "hello world"), then `audio.stop`
- **THEN** the plugin runs ASR on the audio, emits `{"t":"transcript","text":"hello world"}` back to the glasses-app, and forwards "hello world" as a user message to the gateway

#### Scenario: Empty or silent audio

- **WHEN** the accumulated audio contains no detectable speech
- **THEN** the plugin emits `{"t":"transcript","text":""}` and does NOT forward an empty message to the gateway (the glasses-app shows "Didn't catch that" instead)

### Requirement: LiteLLM Whisper ASR backend (preferred when configured)

When `EVEN_G2_ASR_LITELLM_MODEL` is set, the plugin SHALL use LiteLLM's `/v1/audio/transcriptions` endpoint for ASR. The plugin SHALL wrap the accumulated PCM16 bytes as a WAV in-memory (16kHz mono) and POST it to `{EVEN_G2_ASR_LITELLM_BASE_URL or LITELLM_BASE_URL}/v1/audio/transcriptions` with `Authorization: Bearer {EVEN_G2_ASR_LITELLM_API_KEY or LITELLM_API_KEY}`, multipart form `file` (the WAV) + `model` (the configured model name). Latency target: < 1s for a typical 2-3 second utterance when LiteLLM routes to a GPU-optimized backend.

#### Scenario: LiteLLM ASR configured and reachable

- **WHEN** `EVEN_G2_ASR_LITELLM_MODEL=whisper` is set and LiteLLM is reachable
- **THEN** ASR requests POST WAV bytes to LiteLLM's `/v1/audio/transcriptions`, receive `{"text": "..."}`, and the plugin emits the text as a transcript frame

#### Scenario: LiteLLM ASR configured but unreachable

- **WHEN** `EVEN_G2_ASR_LITELLM_MODEL` is set but the LiteLLM request fails (network error, timeout, non-2xx)
- **THEN** the plugin logs a warning and falls back to the faster-whisper CPU backend, then emits the transcript as usual

### Requirement: Parakeet ASR backend on macOS via Apple Neural Engine (optional)

When running on macOS and the parakeet sidecar binary is available, the plugin MAY use `parakeet-tdt-0.6b-v2` via the Swift sidecar for ASR. This path is NOT configured by default — it's only used if LiteLLM ASR is not configured AND `EVEN_G2_ASR_SIDECAR_BIN` points to a valid signed sidecar binary. Latency target: < 500ms for a typical 2-3 second utterance.

#### Scenario: macOS with sidecar available but LiteLLM preferred

- **WHEN** both LiteLLM ASR and parakeet sidecar are configured
- **THEN** LiteLLM ASR takes priority; parakeet is not used

### Requirement: Whisper-tiny CPU fallback

When neither LiteLLM ASR nor parakeet sidecar is available or both fail, the plugin SHALL fall back to `whisper-tiny` via `faster-whisper` running on CPU. Latency target: < 5 seconds for a typical 2-3 second utterance.

#### Scenario: No LiteLLM configured, no sidecar

- **WHEN** `EVEN_G2_ASR_LITELLM_MODEL` is unset and no sidecar binary is configured
- **THEN** ASR requests use `faster-whisper` with the `whisper-tiny` model on CPU

### Requirement: ASR model lazy-loaded on first use

For the faster-whisper CPU backend, the model SHALL be lazily loaded on the first ASR request (not at plugin import time) to avoid blocking gateway startup if the model download is slow. Once loaded, the model SHALL be reused across all subsequent transcription calls within the same process.

For the LiteLLM backend, there is no in-process model to load — each request is a stateless HTTP call. LiteLLM itself may warm up its Whisper backend on its own schedule; that's outside this plugin's scope.

#### Scenario: First faster-whisper request

- **WHEN** the first `audio.stop` frame arrives and LiteLLM is not configured
- **THEN** the faster-whisper model is loaded (downloading weights if necessary on first run), and subsequent requests reuse the loaded model

#### Scenario: Gateway restart

- **WHEN** the gateway restarts and the plugin re-initializes
- **THEN** the faster-whisper model is re-loaded (no persistent state assumed across process restarts)
