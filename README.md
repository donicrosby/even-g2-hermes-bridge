# even-g2-hermes-bridge

Bridge Even Realities G2 smart glasses to a Hermes Agent gateway. Two paths are supported:

## Architecture

### Path A: Full WS plugin (recommended)

```
┌─────────────┐   WSS (Tailscale Serve or reverse proxy)   ┌──────────────────┐
│  glasses-app│ ─────── hello + token ────────────────────▶│  even-g2 plugin  │
│  (on phone) │ ◀── assistant.delta / tool.start/end ───── │  (inside Hermes  │
└─────────────┘    + binary PCM frames for voice           │   Gateway)       │
                                                   ┌───────┴──────────┐
                                                   │  Hermes Gateway  │
                                                   │  (LLM, tools,    │
                                                   │   sessions)      │
                                                   └──────────────────┘
```

- **`plugin/`** — Hermes platform plugin: WS server (port 8767), connection registry, streaming text via `assistant.delta` frames, voice ASR (LiteLLM → faster-whisper fallback), tool-call hooks, session management, QR setup, Tailscale Serve integration. See `plugin/README.md` for install + config.
- **`glasses-app/`** — TypeScript glasses-app using the Even Hub SDK: three text containers (assistant reply + status + session name), touch handlers (tap=toggle mic, double-tap=exit dialog (system confirmation), scroll=switch session), audio streaming, background state persistence, exponential backoff reconnect.

### Path B: BYOA lite (legacy)

```
┌─────────────┐   HTTPS (TLS via Traefik)   ┌──────────────────┐
│  Even Hub   │ ──── POST / ──────────────▶ │  bridge-server   │
│  Add Agent  │    Bearer <BYOA_TOKEN>      │  (FastAPI)       │
└─────────────┘                              └──────┬───────────┘
                                                    ▼
                                             LiteLLM upstream
```

- **`bridge-server/`** — Python FastAPI server speaking the glasses' built-in BYOA protocol. Receives OpenAI chat-completion requests, manages history server-side, deduplicates parallel requests. Simpler than the WS path but no streaming, no tools, no sessions. See `bridge-server/README.md` (if present) or `.env.example`.
- **`docker-compose.yml`** — Docker Compose stack for the BYOA bridge.

## Quick start

### Path A: Full WS plugin (recommended)

```bash
# 1. Build the plugin
cd plugin && uv sync && uv build

# 2. Install into your Hermes Gateway
hermes plugins install ./plugin/
hermes plugins enable even-g2
hermes gateway restart

# 3. Configure (generates token, sets up Tailscale Serve)
hermes even-g2 setup

# 4. Build and install the glasses-app on your phone
cd ../glasses-app && npm install && npm run build
# Package via Even's app loading flow
```

See `plugin/README.md` for full configuration and deployment details.

### Path B: BYOA lite (legacy)

```bash
cp .env.example .env
# Edit .env: set BYOA_TOKEN, LITELLM_API_KEY, CHAT_MODEL
docker compose up -d --build
```

On the phone: Even app → Settings → Add Agent → set URL to `https://your-host` and token to `BYOA_TOKEN`.

## Voice ASR

The WS plugin supports three ASR backends in priority order:

1. **LiteLLM Whisper** — POST PCM-WAV to LiteLLM's `/v1/audio/transcriptions`. Set `EVEN_G2_ASR_LITELLM_MODEL`, `EVEN_G2_ASR_LITELLM_BASE_URL`, `EVEN_G2_ASR_LITELLM_API_KEY`.
2. **Parakeet sidecar** — Swift subprocess (macOS only, not used on Linux).
3. **faster-whisper CPU** — always-available fallback using `whisper-tiny`.

## Known limitations (v1)

- **Single-user**: chat_id keyed by device serial. Multiple users on the same tailnet get separate threads, but there's no multi-tenant ACL on chat_id.
- **Hermes Gateway required**: the plugin runs inside the gateway process, not standalone.
- **Parakeet ASR**: macOS-only, not exercised on Linux deployments. Developer ID signature verification not implemented (out of scope for Linux-first).
- **SDK 0.0.12 limitation**: `setBackgroundState`/`onBackgroundRestore` APIs not yet available in the Even Hub SDK. Background state persistence uses `setLocalStorage`/`getLocalStorage` as a fallback.
- **No HUD error tuning**: the plugin emits standard `error` frames; rendering depends on the glasses-app.

## Repo layout

```
plugin/           Hermes platform plugin (WS server, ASR, hooks, CLI)
glasses-app/      TypeScript glasses-app (Even Hub SDK, page containers)
bridge-server/    Legacy BYOA bridge (FastAPI, docker-compose)
probe/            Integration testing probe (BYOA endpoint validation)
openspec/         Change specs and project archives
```

## Follow-up cleanup

Once the WS path (Path A) is confirmed stable in production:
1. Delete `bridge-server/` (replaced by `plugin/`)
2. Delete legacy `glasses-app/` code (already rewritten)
3. Delete `probe/` (served its purpose during BYOA development)
4. Remove `docker-compose.yml` (only needed for bridge-server)
