## Why

`bridge-server/` currently requires a custom Even Hub SDK plugin (`glasses-app/`) that streams raw PCM16 audio over a WebSocket, runs server-side VAD + Whisper STT, and streams tokens back as ad-hoc JSON frames. Even Hub v0.0.7+ ships a built-in "Add Agent" mode that does on-device STT and POSTs a standard OpenAI chat-completion request to a user-configured URL — making the entire custom plugin + audio pipeline redundant. A real-hardware probe (archived at `openspec/changes/archive/2026-07-20-byoa-probe-spike/observations.md`) confirmed the wire protocol, measured real latency, and surfaced two unexpected behaviors (parallel duplicate requests, 17s cold start) that this migration must handle. Migrating lets the user talk to their LiteLLM-backed agent through the glasses' native UI with zero custom plugin code, drops ~270 lines of audio plumbing, and removes the `webrtcvad` dependency.

## What Changes

- **BREAKING** — Remove the WebSocket endpoint at `/ws/glasses` and all audio-pipeline code (VAD, `webrtcvad` dep, PCM framing, Whisper STT call, streaming chat)
- **BREAKING** — Remove the `Session` dataclass as currently shaped (audio-buffer fields go away; replaced by a slim per-session history holder)
- **Add** `POST /` handler that accepts the BYOA request shape (`{"model":"openclaw","messages":[{"role":"user","content":"..."}]}`)
- **Add** Bearer token enforcement via `BYOA_TOKEN` env var (rejected with 401 on mismatch — unlike the probe, production enforces)
- **Add** Server-side conversation history keyed by client IP (probe confirmed glasses send only the current user message, never history)
- **Add** Request deduplication (probe confirmed glasses fire duplicate parallel requests per utterance; without dedup every utterance costs 2× LLM calls)
- **Add** LiteLLM prewarm on server startup (probe measured 17.3 s cold-start; prewarm shifts that cost off the first user utterance)
- **Add** gzip on responses (glasses send `accept-encoding: gzip`)
- **Add** `truststore` dependency + `truststore.inject_into_ssl()` at import time so httpx validates LiteLLM's local-CA-signed cert via the system trust store (drops the `SSL_CA_FILE` env var)
- **Add** `GET /health` (unauthenticated) for docker-compose / Traefik health checks
- **Migrate** `bridge-server/` from `requirements.txt` to `pyproject.toml` + `uv_build` + `src/` layout per repo-root `AGENTS.md`
- **Update** `docker-compose.yml` and `Dockerfile` for the new launch command (`uv run uvicorn ...` instead of `python main.py`)
- **Update** `.env.example` to drop audio/VAD vars, drop `SSL_CA_FILE`, add `BYOA_TOKEN`
- **Delete** `bridge-server/test-client.html` (debug tool for the WS protocol — no longer applies)
- **No change** to `glasses-app/` in this change (it remains as a legacy fallback path; a future change can delete it once BYOA is confirmed stable)

## Capabilities

### New Capabilities
- `byoa-endpoint`: The HTTP `POST /` chat-completions endpoint that the G2 glasses' built-in "Add Agent" mode calls. Covers request validation, Bearer auth, response shape, gzip, error rendering, and the `/health` endpoint.
- `session-history`: Server-side conversation history reconstructed per glasses client (glasses do not send history). Covers history storage, key derivation, max-turn cap, and injection into forwarded LiteLLM requests.
- `request-dedup`: Coalescing of duplicate parallel requests the glasses fire per utterance. Covers dedup key, time window, cache hit behavior, and concurrency safety.

### Modified Capabilities
<!-- None — no existing specs in openspec/specs/ to modify. -->

## Impact

- **Code**: ~270 lines deleted from `bridge-server/main.py` (audio pipeline + WS handler); ~150 lines added (BYOA handler, history, dedup, prewarm). Net smaller and simpler.
- **Dependencies**: Drop `webrtcvad>=2.0.10`. Add `truststore>=0.10.0`. Keep `fastapi`, `uvicorn[standard]`, `httpx`. Drop `setuptools` (no longer needed with `uv_build`). Drop `python-json-logger` (replaced with stdlib logging; the JSON formatter isn't worth the dep for the smaller log surface).
- **Configuration**: New required env var `BYOA_TOKEN`. Removed env vars: `VAD_AGGRESSIVENESS`, `SILENCE_FRAMES`, `MIN_SPEECH_FRAMES`, `LOOKBACK_FRAMES`, `SSL_CA_FILE`, `WHISPER_MODEL`. Renamed: none.
- **Deployment**: Dockerfile entrypoint changes from `python main.py` to `uv run uvicorn byoa_bridge.server:app ...`. docker-compose port unchanged (8765), but Traefik routing labels change from WS to HTTP.
- **User-facing**: User must reconfigure Even app's "Add Agent" entry to point at the server URL + set the matching token. The custom `glasses-app/` plugin is no longer the primary path — but it's not deleted in this change, so users who rely on it can keep using it until a follow-up cleanup.
- **Security posture improves**: Bearer auth enforced (WS endpoint had none). Server-side history keyed by client IP is still weak isolation (anyone on the LAN sharing an IP collides) but no worse than the status quo.
- **Rollback**: Plain `git revert` of the change. The `glasses-app/` plugin still works against the old WS endpoint if deployed alongside — but the WS endpoint is gone after this change, so rollback means redeploying the pre-migration image.
