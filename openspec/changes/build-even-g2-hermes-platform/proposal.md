## Why

Two spikes proved we cannot solve the slow-agent problem on the glasses' built-in Add Agent path:
- `archive/2026-07-20-byoa-probe-spike` confirmed the BYOA wire protocol works for short responses but the glasses fire duplicate parallel requests and send no conversation history.
- `archive/2026-07-20-sse-tolerance-spike` definitively ruled out SSE pass-through — the glasses' Dart HTTP client rejects `Content-Type: text/event-stream` responses with "network error" before parsing the body.

The architecturally correct answer is to bypass Even's built-in Add Agent entirely. The huntsyea/hermes-evenhub-bridge project (MIT-licensed, well-documented) has already proven this pattern: a custom Even Hub SDK app (TypeScript) speaks a bidirectional JSON-over-WebSocket protocol with a Python Hermes platform adapter. The WS connection is persistent, so the agent can take minutes to respond without any client-side timeout. Tokens stream as deltas. Tool-call activity surfaces as `tool.start`/`tool.end` frames. Sessions, pairing, and the Hermes Gateway integration all come via the standard `BasePlatformAdapter` interface — we inherit Hermes's async patterns (the same ones Signal, Discord, and Telegram use) for free.

We build our own rather than adopting huntsyea's directly because: (1) we want first-class Hermes Gateway config integration that matches our existing `hermes.local` deployment, (2) we want to own the WS protocol so we can evolve it without waiting for upstream releases, (3) we already have a working Tailscale + TLS setup that's proven with the prior BYOA path, (4) the existing `bridge-server-byoa-migration` stays useful as a "lite" sync-only path for users who don't want to install a custom glasses-app — building our own lets us share code and conventions across both paths.

## What Changes

- **Add** `plugin/` directory at repo root — a Hermes platform plugin package (`byoa_plugin` or similar, Python) that:
  - Inherits from `BasePlatformAdapter` (the same interface Signal/Discord/Telegram use)
  - Registers `even_g2` as a Hermes platform via `ctx.register_platform(...)`
  - Hosts a WebSocket server (default port 8767) that the glasses-app connects to
  - Authenticates glasses via shared `EVEN_G2_BRIDGE_TOKEN` (constant-time compared)
  - Forwards inbound user messages to the Hermes Gateway via the standard platform API
  - Receives outbound assistant messages via `send_message` / `edit_message` and pushes them as `assistant.delta` frames to the right glasses connection
  - Surfaces tool-call activity via `pre_tool_call` / `post_tool_call` hooks → `tool.start` / `tool.end` frames
  - Manages the `chat_id ↔ ws connection` mapping in a thread-safe registry
  - Computes deltas via `StreamState.delta_for()` so the gateway's accumulated text becomes incremental append-only frames on the wire
  - Supports `: ping` keepalives on the WS to defeat any idle proxies
- **Replace** `glasses-app/` contents — rewrite the existing TypeScript Even Hub SDK app to use the new WS protocol (the old code targeted the legacy `bridge-server/` WebSocket audio pipeline; the new code targets the Hermes platform plugin). Keeps the same directory name and Vite/TS scaffold:
  - Connects via WS to the bridge using `EVEN_G2_BRIDGE_TOKEN`
  - Sends JSON frames: `hello + token`, `text`, `audio.start` / `audio.stop` + binary PCM, `sessions.list` / `sessions.switch` / `sessions.new`, `stop`
  - Receives JSON frames: `assistant.delta` (incremental text), `tool.start`, `tool.end`, `turn.done`, `sessions`, `active`, `history`, `transcript`, `error`
  - Renders the running accumulated assistant text to the HUD via `textContainerUpgrade` (in-place update, max 2000 chars per call per SDK docs)
  - **Renders session names** (from Hermes session metadata) in a bounded/scrolling text container — long names truncate with ellipsis or scroll horizontally
  - **Persists state across background/foreground transitions** via `setBackgroundState` / `onBackgroundRestore` — accumumulated assistant text, current session id, connection state, and last-received frame survive the Even Hub host's Headless WebView migration (per the `background-state` skill)
  - **Best-effort foreground activation on assistant response** — when an `assistant.delta` frame arrives while the app is backgrounded, the app attempts `bridge.callEvenApp('bringToFront')` (undocumented SDK escape hatch). If the host doesn't support it, the app silently falls back to updating the HUD via the headless WebView (which the user sees when they next open the app)
  - Handles touch input (long-press = new turn; double-press = interrupt; scroll = session switch)
  - Shows session UI (list of Hermes sessions by name, switch between them)
  - Phone-mic fallback when G2 four-mic is unavailable (declare both `g2-microphone` and `phone-microphone` permissions)
- **Add** `plugin/protocol.py` — single source of truth for the WS JSON frame schema, shared between plugin and glasses-app via a generated TypeScript type file (`plugin/protocol.ts` emitted at build time)
- **Add** `plugin/setup_flow.py` — Tailscale Serve integration: `tailscale serve --https=8443 --bg http://127.0.0.1:8767` to expose the local WS as a private `wss://...ts.net:8443` URL the phone can reach
- **Add** `plugin/asr/` — voice transcription via three backends, tried in order:
  1. **ROCm-optimized Whisper via LiteLLM** — when `EVEN_G2_ASR_LITELLM_MODEL` is set (e.g., `whisper`), the plugin POSTs PCM-WAV to LiteLLM's `/v1/audio/transcriptions` endpoint. Best fit for the user's `home.arpa` host (GPU + ROCm on Linux — no Apple Neural Engine, but plenty of GPU compute).
  2. **`faster-whisper` on local CPU** — universal fallback when neither LiteLLM nor parakeet is configured.
  3. **`parakeet-tdt-0.6b-v2` via Swift sidecar** — Apple Neural Engine path (only relevant if user runs the gateway on macOS in the future; not the primary path for `home.arpa`).
- **Add** `plugin/qr_setup.py` — generates a QR code encoding the bridge URL + token as a `hermes-even-g2://configure?url=<...>&token=<...>` deep link (or a plain `wss://` URL + token pair if deep links aren't supported). Renders to a PNG via `qrcode` library, prints to terminal, and serves on a tiny HTTP endpoint (`GET /qr` on the WS port) so the user can scan it from the phone camera to bootstrap the glasses-app without manual URL/token entry. This unblocks fast local iteration without sideloading the app a billion times.
- **Add** env-driven Hermes plugin auto-configuration (`EVEN_G2_BRIDGE_TOKEN`, `EVEN_G2_BRIDGE_HOST`, `EVEN_G2_BRIDGE_PORT`, `EVEN_G2_BRIDGE_PUBLIC_URL`, `EVEN_G2_HOME_CHANNEL`, `EVEN_G2_ALLOWED_USERS`, `EVEN_G2_ALLOW_ALL_USERS`, `EVEN_G2_ASR_LITELLM_MODEL`, `EVEN_G2_ASR_LITELLM_BASE_URL`, `EVEN_G2_ASR_LITELLM_API_KEY`)
- **Update** root `README.md` — describe the dual architecture: BYOA "lite" path (`bridge-server/`) for sync/short responses via Even's built-in Add Agent, full WS path (`plugin/` + `glasses-app/`) for streaming/long responses via the custom SDK app
- **Keep** `bridge-server/` as-is — the BYOA path remains a working fallback for users who don't want to install the custom glasses-app
- **No changes** to `docker-compose.yml` for the BYOA bridge (still runs as-is) — new plugin runs inside the Hermes Gateway process on `hermes.local`, not as a docker-compose service

## Capabilities

### New Capabilities
- `hermes-platform-plugin`: The Python Hermes platform adapter that bridges the glasses-app to the Hermes Gateway. Covers WebSocket server, hello/pairing handshake, `BasePlatformAdapter` interface implementation, streaming via `send_message`/`edit_message` deltas, tool-call hooks, chat_id registry, env-driven config, Tailscale Serve setup.
- `glasses-ws-app`: The TypeScript Even Hub SDK app that runs on the phone, captures audio/text input, renders assistant deltas to the glasses HUD, and handles session switching and touch input.
- `glasses-ws-protocol`: The JSON-over-WebSocket frame schema spoken between the plugin and the glasses-app. Covers all frame types, handshake, auth, binary audio framing, keepalives, error frames.
- `voice-asr`: Voice transcription via parakeet (Apple Neural Engine) or whisper-tiny (CPU fallback). Activated when the glasses-app sends `audio.start` frames; the plugin runs ASR and emits `transcript` frames back.

### Modified Capabilities
<!-- None — no existing main specs to modify. -->

## Impact

- **Code**: Adds ~1500 lines of rewritten TypeScript (`glasses-app/`, replacing the prior implementation) and ~1200 lines of new Python (`plugin/`). `glasses-app/` is a full rewrite (old code deleted, new code written) but the directory/Vite scaffold stays. `plugin/` is entirely new.
- **Dependencies**: 
  - `plugin/`: `websockets>=16.0`, `numpy>=1.26`, `faster-whisper>=1.2.1`, `qrcode>=7.4`, `httpx>=0.27.0` (for LiteLLM ASR call) — all via `uv` + `pyproject.toml` per `AGENTS.md`
  - `glasses-app/`: `@evenrealities/even_hub_sdk@^0.0.12`, dev deps for Vite + TypeScript (already present; refresh during rewrite)
- **Deployment**: Two new deployment targets:
  1. **Hermes Gateway plugin** — installed via `hermes plugins install ./plugin/` (or published to Hermes plugin registry and installed by name) on `hermes.local`. Runs inside the gateway process. Configured via Hermes dashboard / `hermes` CLI / env vars.
  2. **Even Hub companion app** — `glasses-app/` rebuilt and packaged via Even's app packaging flow (Vite + `ehpk`), installed on the user's phone via Even's app loading mechanism (QR code or sideload). Replaces the prior glasses-app build.
- **Operational**:
  - Hermes Gateway on `hermes.local` must be restart-able to load the plugin (`hermes gateway restart`)
  - Tailscale Serve must be available on the gateway host (`tailscale serve --https=8443 ...`) to expose the local WS as a private WSS URL
  - Even Hub companion app must be installed on the user's phone (replaces or supplements the BYOA "Add Agent" entry)
- **Security**:
  - WS auth via shared `EVEN_G2_BRIDGE_TOKEN` (constant-time compared, same pattern as the existing `byoa-bridge` BYOA_TOKEN)
  - Tailscale Serve provides transport-level auth (only devices on the user's Tailscale tailnet can reach the WSS endpoint)
  - Plugin runs with Hermes Gateway's privileges; inherits the gateway's auth model for upstream calls (no separate LiteLLM key needed — gateway owns that)
- **Rollback**: 
  - Disable plugin: `hermes plugins disable even_g2 && hermes gateway restart`
  - Uninstall the rebuilt glasses-app from phone, reconfigure Even app → Add Agent to point at the existing `bridge-server/` BYOA endpoint
  - Both paths coexist during rollout — no downtime
- **Migration**: User runs BYOA bridge + plugin side-by-side during the transition. Once the rebuilt glasses-app is stable for N days, the user can stop using the BYOA Add Agent entry and rely on the glasses-app exclusively. A future change can then delete `bridge-server/`.
