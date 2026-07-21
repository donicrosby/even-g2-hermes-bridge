## Context

Two archived spikes shaped this design:

1. **`archive/2026-07-20-byoa-probe-spike`** — confirmed the G2 BYOA wire protocol shape (POST `/`, Bearer auth, OpenAI chat-completion JSON response). Surfaces: server-side history is required (glasses send no history), deduplication is required (glasses fire parallel duplicate requests), 17s cold-start needs prewarm.

2. **`archive/2026-07-20-sse-tolerance-spike`** — definitively ruled out SSE pass-through. The glasses' Dart HTTP client rejects `Content-Type: text/event-stream` with "network error" before parsing the body, for both OpenResponses and OpenAI chat.completion.chunk flavors. The BYOA path is structurally limited to synchronous request/response — no way to stream tokens, no way to survive long agent responses.

Public reference: `huntsyea/hermes-evenhub-bridge` (MIT) has shipped a working implementation of exactly what we need:
- Custom Even Hub SDK app (TS) → WS JSON protocol → Python Hermes platform plugin → Hermes Gateway
- `BasePlatformAdapter` subclass with `send_message` / `edit_message` / streaming-via-deltas
- Tool-call hooks (`pre_tool_call` → `tool.start` frame, `post_tool_call` → `tool.end` frame)
- Tailscale Serve integration for private WSS endpoint
- Voice ASR via parakeet (Apple Neural Engine) or whisper-tiny (CPU fallback)

The existing `bridge-server-byoa-migration` we shipped is the right answer for the sync/short-response path. It stays. This change adds the **async/streaming path** alongside it — not a replacement.

Constraints:
- Repo Python policy (`AGENTS.md`): all Python via `uv` + `pyproject.toml` + `uv_build` backend + `src/<package>/` layout
- User's Hermes Gateway is at `hermes.local`, reachable over Tailscale
- User has a working local CA for TLS (proven by prior BYOA probe)
- Even Hub SDK 0.0.12 is the current target (`@evenrealities/even_hub_sdk@^0.0.12`)
- This repo already has `glasses-app/` (legacy WS plugin for the old bridge-server) — keep as reference, do not modify

## Goals / Non-Goals

**Goals:**
- Build a Hermes platform plugin (`plugin/`) that registers `even_g2` as a first-class platform, exactly the same way Telegram/Discord/Signal are registered
- Build a new glasses-app (rewriting `glasses-app/` in place) that uses the Even Hub SDK to capture voice/text input and render streaming assistant deltas to the 576×288 HUD
- Define a JSON-over-WebSocket protocol that both ends speak — bidirectional, persistent connection, no per-message timeouts
- Inherit Hermes's streaming pattern: `send_message` creates the initial HUD text, `edit_message` updates it in place via deltas, `edit_message(finalize=True)` marks it done
- Surface tool-call activity as `tool.start` / `tool.end` frames so the user sees "🔍 Searching the web..." on the HUD while the agent runs tools
- Voice transcription via on-device parakeet (preferred) or faster-whisper CPU fallback
- Make it installable via `hermes plugins install ./plugin/` and configurable via Hermes dashboard / CLI / env vars
- Coexist with the existing `bridge-server/` BYOA path — user can use either; both can run simultaneously
- Stay interoperable with huntsyea's `hermes-even-hub-app` if possible (reuse their WS protocol verbatim where it makes sense)

**Non-Goals:**
- Replace `bridge-server/` — stays as the BYOA "lite" path
- Delete `glasses-app/` (legacy) — stays as reference; separate cleanup change later
- Build a new agent runtime — we use the existing Hermes Gateway at `hermes.local`
- Build LiteLLM integration in the plugin — gateway owns upstream LLM routing; we just speak platform-adapter API
- Multi-tenant support — single-user deployment, like the BYOA bridge
- BYOA-mode backward compatibility for the rebuilt glasses-app (it requires the plugin; doesn't work standalone)
- Custom HUD layouts (use standard text container with in-place updates; no fancy UI for v1)
- Image/file support (text + voice only for v1; can add input_image / input_file later)
- WebRTC audio (the SDK gives us PCM16 16kHz mono via `audioControl(true, AudioInputSource.Glasses)`; we use that)
- Authentication beyond the shared bridge token (no per-user ACLs, no OAuth, no mTLS for v1)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phone (companion app, runs glasses-app in WebView)                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  glasses-app/  (TypeScript, Even Hub SDK 0.0.12 — rewritten)           │  │
│  │                                                                       │  │
│  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐   │  │
│  │  │ Audio capture│   │ Touch handler│   │ HUD renderer              │   │  │
│  │  │ (glasses mic │   │ (long-press= │   │ (textContainerUpgrade     │   │  │
│  │  │  via SDK)    │   │  new turn,   │   │  for streaming deltas)    │   │  │
│  │  │              │   │  dbl=stop)   │   │                           │   │  │
│  │  └──────┬───────┘   └──────┬───────┘   └────────────▲─────────────┘   │  │
│  │         │                  │                        │                  │  │
│  │         ▼                  ▼                        │                  │  │
│  │  ┌──────────────────────────────────────────────────┴──────────────┐   │  │
│  │  │  WS client (JSON frames + binary PCM)                           │   │  │
│  │  │  Outbound: hello+token, text, audio.start/stop + binary,        │   │  │
│  │  │            sessions.list/switch/new, stop                       │   │  │
│  │  │  Inbound: assistant.delta, tool.start/end, turn.done,           │   │  │
│  │  │           sessions, active, history, transcript, error          │   │  │
│  │  └───────────────────────────┬─────────────────────────────────────┘   │  │
│  └──────────────────────────────┼─────────────────────────────────────────┘  │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │  WSS (Tailscale Serve)
                                  │  wss://hermes.your-tailnet.ts.net:8443
                                  │  Auth: Bearer EVEN_G2_BRIDGE_TOKEN
                                  │  : ping every 30s
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Hermes Gateway host (hermes.local)                                     │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  plugin/  (Python, runs in-process with Hermes Gateway)               │  │
│  │                                                                       │  │
│  │  ┌──────────────────────────────────────────────────────────────┐     │  │
│  │  │  WS server (port 8767, bound to 127.0.0.1)                   │     │  │
│  │  │  - accepts connections, validates hello+token                 │     │  │
│  │  │  - maps chat_id (device id from hello) → ws connection       │     │  │
│  │  │  - parses inbound frames, dispatches to handlers             │     │  │
│  │  │  - pushes outbound frames to the right ws connection         │     │  │
│  │  │  - sends : ping every 30s                                    │     │  │
│  │  └────────────┬──────────────────────────────────────┬────────┘     │  │
│  │               │ inbound                              │ outbound     │  │
│  │               ▼                                      ▲              │  │
│  │  ┌──────────────────────┐         ┌─────────────────────────────┐   │  │
│  │  │  EvenG2Adapter        │         │  StreamState                │   │  │
│  │  │  (BasePlatformAdapter)│         │  - delta_for(accumulated)   │   │  │
│  │  │                       │         │    returns new suffix        │   │  │
│  │  │  handle_message()     │         │  - strips streaming cursor   │   │  │
│  │  │  → Gateway            │         │    (" ▉") before diffing    │   │  │
│  │  │                       │         └─────────────────────────────┘   │  │
│  │  │  send_message()       │ ← called by Gateway with                  │  │
│  │  │  edit_message()       │   accumulated reply text                  │  │
│  │  │  → StreamState diff   │                                          │  │
│  │  │  → send_frame(        │                                          │  │
│  │  │      assistant.delta) │                                          │  │
│  │  └───────────┬───────────┘                                          │  │
│  │              │                                                       │  │
│  │              │ pre_tool_call / post_tool_call hooks                  │  │
│  │              │ → tool.start / tool.end frames                        │  │
│  │              │                                                       │  │
│  │  ┌───────────▼───────────────────────────────────────────────────┐  │  │
│  │  │  Voice ASR (asr/)                                              │  │  │
│  │  │  - parakeet-tdt-0.6b-v2 via Swift sidecar (Apple Neural Engine)│  │  │
│  │  │  - whisper-tiny via faster-whisper (universal CPU fallback)   │  │  │
│  │  │  - input: PCM16 16kHz mono from audio frames                  │  │  │
│  │  │  - output: transcript frame back to glasses-app               │  │  │
│  │  └───────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │  Hermes platform API
                                  │  (in-process function calls)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Hermes Gateway  (existing, running on hermes.local)                    │
│  - Agent runtime (hermes/hermes-agent)                                      │
│  - Sessions, memory, tools                                                  │
│  - Streaming via edit_message pattern                                       │
│  - Routes inbound from platform adapter to agent                            │
│  - Routes outbound from agent back via send_message / edit_message          │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Decisions

### D1: New `plugin/` directory + in-place rewrite of `glasses-app/`

**Choice:** `plugin/` (Python Hermes platform plugin package) is new at repo root. `glasses-app/` (TypeScript Even Hub SDK app) is rewritten in place — old WS-audio-bridge code deleted, new Hermes-platform-WS code written in the same directory, keeping the Vite/TS scaffold.
**Rationale:** `plugin/` is additive (no risk of breaking the working BYOA path during development). `glasses-app/` rewrite-in-place avoids the confusion of having two TS app directories — there's only ever one "current" glasses-app. The old code targeted the now-obsolete WS audio pipeline; the new code targets the Hermes platform plugin. Keeping the directory name simplifies build pipelines, README references, and packaging.
**Alternatives considered:** Put plugin inside `bridge-server/` (mixes two unrelated Python packages — BYOA HTTP server vs Hermes platform adapter). Create `glasses-app-v2/` alongside `glasses-app/` (user explicitly rejected this — too confusing; just redo glasses-app).

### D2: Reuse huntsyea's WS protocol verbatim

**Choice:** Adopt `huntsyea/hermes-evenhub-bridge`'s JSON frame schema as-is. Frame types:
- Client→Server: `hello` (with token + device id), `text`, `audio.start`, `audio.stop` (+ binary frames), `sessions.list`, `sessions.switch`, `sessions.new`, `stop`
- Server→Client: `assistant.delta`, `assistant`, `tool.start`, `tool.end`, `turn.done`, `sessions`, `active`, `history`, `transcript`, `error`
- Binary: raw PCM16 16kHz mono audio frames between `audio.start` and `audio.stop`
**Rationale:** Their schema is well-thought-out and proven in production. Reusing it gives us interop with their `hermes-even-hub-app` glasses-app if we ever want to mix-and-match. Avoids a new protocol design cycle. We can extend it later if we find gaps.
**Alternatives considered:** Design our own (weeks of iteration; no interop benefit). Adopt a different existing protocol (none fit — OpenClaw is sync HTTP, MCP is for tools not chat, Matrix is too heavy).

### D3: Plugin runs in-process with Hermes Gateway

**Choice:** The plugin is loaded by `hermes plugins install ./plugin/` and runs inside the Hermes Gateway process. It does NOT run as a separate docker-compose service or standalone daemon.
**Rationale:** This is how all Hermes platform plugins work — `BasePlatformAdapter` is called directly by the gateway in-process. No IPC, no auth boundary between plugin and gateway, no separate process to manage. The plugin hosts its own WS server (bound to loopback by default) and Tailscale Serve exposes it.
**Trade-off:** Plugin must be installed on the gateway host (not on a separate machine). For the user's setup, this means the plugin runs on `hermes.local`. The existing `bridge-server/` (BYOA path) can still run elsewhere if desired.
**Alternatives considered:** Standalone Python service that talks to the gateway via HTTP (loses the in-process platform API; we'd be back to the BYOA shape, just over WS to the glasses).

### D4: Delta-based streaming via `StreamState`

**Choice:** Implement the same `StreamState.delta_for()` pattern huntsyea uses:
1. Gateway calls `send_message(chat_id, "Hello")` → adapter shows "Hello" on HUD via `assistant.delta` frame with delta="Hello"
2. Gateway calls `edit_message(chat_id, msg_id, "Hello world")` → adapter diffs against previous → sends `assistant.delta` with delta=" world"
3. Gateway calls `edit_message(chat_id, msg_id, "Hello world.", finalize=True)` → adapter diffs → sends `assistant.delta` with delta="." then `turn.done`
4. If accumulated text ends with the streaming cursor (` ▉`), strip it before diffing (so the cursor doesn't appear in deltas)
**Rationale:** This is how every Hermes platform handles streaming. It's the standard pattern, well-tested in Signal/Discord/Telegram. The glasses-app just appends each delta to its existing HUD text via `textContainerUpgrade`, giving the user a token-by-token appearance.
**Alternatives considered:** Send the full accumulated text every time (waste of bandwidth; requires full-text redraws on HUD instead of appends). Send only the new tokens from the gateway's stream (would require the gateway to expose its token stream — it doesn't; it just calls edit_message with accumulated text).

### D5: Auth via shared `EVEN_G2_BRIDGE_TOKEN`, constant-time compared

**Choice:** Glasses-app sends `hello` frame with `token: <EVEN_G2_BRIDGE_TOKEN>` on connect. Plugin validates via `hmac.compare_digest(token, expected_token)`. On mismatch: close WS with code 1008 "unauthorized".
**Rationale:** Same pattern as the existing `bridge-server/` BYOA_TOKEN and the prior probes. Constant-time comparison prevents timing attacks on the token. WS close code 1008 (Policy Violation) is the standard "you're not allowed here" code per RFC 6455.
**Alternatives considered:** Per-device tokens with pairing flow (overkill for v1; can add later). mTLS via Tailscale cert (Tailscale already provides transport auth; the token is belt-and-suspenders).

### D6: `chat_id` derived from hello frame's `device` field, falls back to "g2"

**Choice:** The `hello` frame includes a `device` field (glasses serial number from `bridge.getDeviceInfo()`). That becomes the `chat_id` used for routing. If absent, default to `"g2"`.
**Rationale:** `chat_id` is the key the Hermes Gateway uses to route outbound messages back to the right client. Using the device serial number means multiple glasses pairs can connect simultaneously without colliding. The fallback handles older glasses-app versions or SDK quirks.
**Alternatives considered:** Generate a UUID per connection (loses cross-restart session continuity — Hermes would see a new chat_id each reconnect and start a fresh session). Use client IP (collisions on NAT).

### D7: ASR via ROCm-optimized Whisper on LiteLLM (primary), faster-whisper CPU (fallback), parakeet on macOS (future)

**Choice:** ASR backends tried in priority order:
1. **LiteLLM Whisper** — when `EVEN_G2_ASR_LITELLM_MODEL` is set (default for the user's setup: `whisper`), the plugin wraps PCM bytes as WAV in-memory and POSTs to `{LITELLM_BASE_URL}/v1/audio/transcriptions` with the model name. This routes the request through the user's LiteLLM proxy to the ROCm-optimized Whisper backend on the your hardware GPU. Target latency: < 1s for a 2-3s utterance.
2. **`faster-whisper` on local CPU** — universal fallback when LiteLLM is unreachable or `EVEN_G2_ASR_LITELLM_MODEL` is unset. Uses `whisper-tiny` model. Slower (~2-5s) but works on any host.
3. **`parakeet-tdt-0.6b-v2` via Swift sidecar** — only relevant if user moves the gateway to macOS in the future; not configured by default. Documented as an optional path.

**Rationale:** The user's `home.arpa` is a your gateway host on Linux — no Apple Neural Engine, but the user has ROCm-optimized Whisper available via LiteLLM. Routing ASR through LiteLLM (instead of running Whisper locally in the plugin process) has three advantages: (1) it reuses the existing LiteLLM infra + ROCm setup that's already tuned, (2) it keeps the plugin process lightweight (no model weights loaded in-process), (3) it works regardless of which host the plugin runs on, as long as LiteLLM is reachable. The `faster-whisper` fallback covers the case where LiteLLM is briefly unreachable.
**Trade-off:** One extra network hop (plugin → LiteLLM → Whisper). On `home.arpa` the latency is negligible (same LAN). For remote deployments this could add ~100ms. Acceptable.
**Alternatives considered:** Run `faster-whisper` with a larger model locally on the the gateway host's CPU (slower than ROCm GPU). Use parakeet (requires macOS — user is on Linux). Cloud STT via OpenAI (privacy + latency + cost).

### D8: Flexible network exposure — Tailscale Serve OR user-provided reverse proxy

**Choice:** The plugin's WS server binds to a configurable host (default `127.0.0.1:8767`). Three deployment patterns are supported:

1. **Tailscale Serve** (default for users with Tailscale): `tailscale serve --https=8443 --bg http://127.0.0.1:8767` exposes the local WS as `wss://<magic-dns>:8443`. The setup flow (`hermes even-g2 setup`) auto-detects Tailscale and runs the serve command.

2. **User-provided reverse proxy** (for public/internet-facing deployments): The user runs their own nginx/caddy/traefik in front of the plugin. The reverse proxy terminates TLS and forwards to the plugin's bind address. The user sets `EVEN_G2_BRIDGE_PUBLIC_URL=wss://hermes.example.com` (or whatever their public URL is) so the QR generator and `hermes even-g2 qr` advertise the correct external URL.

3. **Direct bind** (LAN-only, no TLS — for development): Set `EVEN_G2_BRIDGE_HOST=0.0.0.0`. The plugin binds to all interfaces directly. No TLS termination; only suitable for trusted LANs or local development. Even Hub may reject this in production (per the BYOA probe findings).

**Config resolution for the advertised URL** (used in QR codes, CLI output, dashboard):
```
Priority order (highest first):
  1. EVEN_G2_BRIDGE_PUBLIC_URL (explicit override) — always wins if set
  2. Tailscale MagicDNS URL from `tailscale status --json` — auto-detected if Tailscale is available
  3. LAN URL `wss://<lan-ip>:<port>` — last-resort fallback
```

**Rationale:** User explicitly asked for non-Tailscale support: "an actual externally facing webhost, behind a reverse proxy with SSL." Three real deployment shapes matter: (a) personal tailnet via Tailscale Serve (the default), (b) public-facing deployment via user's existing reverse proxy infra (the new case), (c) LAN-only for local development. Making `EVEN_G2_BRIDGE_PUBLIC_URL` the explicit override means any deployment shape works as long as the user tells us the externally-visible URL.

**Setup flow behavior:**
- `hermes even-g2 setup` with Tailscale available → runs `tailscale serve`, auto-sets `EVEN_G2_BRIDGE_PUBLIC_URL` from MagicDNS
- `hermes even-g2 setup` without Tailscale → detects absence, prints "Tailscale not detected. Set `EVEN_G2_BRIDGE_PUBLIC_URL` to your externally-visible WSS URL (e.g., `wss://hermes.example.com`) and configure your reverse proxy to forward to `http://<this-host>:<port>`." Does NOT fail; just skips the Tailscale step.

**Trade-off:** No automatic TLS in the reverse-proxy path — the user is responsible for their own cert (Let's Encrypt, internal CA, etc.). This is standard practice for self-hosted services; we don't need to reinvent it.
**Alternatives considered:** Hard-require Tailscale (loses public-deployment users). Bundle Caddy/nginx in the plugin (way out of scope — plugin should be a Hermes plugin, not a web server). Add built-in TLS termination via `truststore` + `SSL_CERT_FILE` like the BYOA bridge (works but redundant when the user already has a reverse proxy; we'd have two TLS terminators).

### D9: Plugin packaged as a Hermes "directory plugin" with `pyproject.toml`

**Choice:** `plugin/pyproject.toml` declares the package metadata + deps (websockets, numpy, faster-whisper). Hermes discovers it via `plugin/` directory layout + `register(ctx)` function in `plugin/__init__.py`. Install via `hermes plugins install ./plugin/`.
**Rationale:** This is the canonical Hermes plugin shape (per Context7 docs and the huntsyea reference). Matches `AGENTS.md` Python policy: `uv` + `pyproject.toml` + `uv_build` backend + `src/<package>/` layout.
**Alternatives considered:** Publish to PyPI and install via `pip install` (loses Hermes's plugin discovery; harder to develop locally). Single-file plugin (too much code for one file).

### D10: `protocol.py` is the single source of truth; `protocol.ts` is generated

**Choice:** Define all frame schemas in `plugin/src/byoa_plugin/protocol.py` as `TypedDict` / `dataclass`. A build-time script emits `glasses-app/src/protocol.ts` with matching TypeScript types. Both ends import from their respective generated/source file.
**Rationale:** One schema to maintain. Drift between Python and TS sides is caught at build time. If the schema changes, regeneration + TS compile will fail until both ends are updated.
**Alternatives considered:** Two hand-maintained schema files (drift inevitable). JSON Schema as the source of truth with both Python and TS generators (heavier tooling; the schemas are simple enough that direct generation is cleaner).

### D11: Keep existing `bridge-server/` untouched; rewrite `glasses-app/` in place

**Choice:** `bridge-server/` stays as-is — the BYOA path remains a working fallback. `glasses-app/` is rewritten in place (old WS-audio code deleted, new Hermes-WS code written).
**Rationale:** The BYOA path gives the user a migration runway — if the new glasses-app hits snags, they can fall back to Even's built-in Add Agent pointed at `bridge-server/`. The glasses-app rewrite-in-place means there's only one TS app to maintain; no v1/v2 confusion.
**Alternatives considered:** Delete `bridge-server/` now (loses the fallback path during the riskiest first deployment). Keep both `glasses-app/` (old) and `glasses-app-v2/` (new) (user rejected — too confusing).

### D12: Even Hub SDK 0.0.12 features we use

**Choice:** Use these SDK features specifically:
- `waitForEvenAppBridge()` — wait for SDK ready
- `createStartUpPageContainer([{textContainer for assistant reply}, {textContainer for status line}, {textContainer for session name}])` — one-shot page setup with three containers
- `audioControl(true, AudioInputSource.Glasses)` — enable glasses mic (requires startup page first)
- `onEvenHubEvent(cb)` — receive `audioEvent` (PCM frames), `textEvent` (touch), `sysEvent` (lifecycle, foreground/background)
- `textContainerUpgrade({containerID, content})` — in-place text update for streaming deltas (max 2000 chars per call)
- `rebuildPageContainer(...)` — full redraw for session switches
- `getDeviceInfo()` — read serial number for the `hello.device` field
- `onDeviceStatusChanged(cb)` — detect disconnects/reconnects
- `setLocalStorage(k, v)` / `getLocalStorage(k)` — persist bridge URL + token across app restarts (per device-features skill: browser IndexedDB/localStorage are unreliable in the Flutter WebView)
- `setBackgroundState(key, exporter)` / `onBackgroundRestore(key, restorer)` — survive the Even Hub host's Headless WebView migration when the phone goes to background (per the `background-state` skill)
- `callEvenApp(method, params?)` — low-level escape hatch for undocumented native methods; used for best-effort foreground activation (see D14)
**Rationale:** These are the SDK APIs that match our needs (per the loaded `sdk-reference` and `background-state` skills). `textContainerUpgrade` is the right call for streaming — full redraws via `rebuildPageContainer` would flicker. `setBackgroundState` / `onBackgroundRestore` are REQUIRED because the WS connection will be killed when the WebView migrates to headless; we need to restore accumulated assistant text, current session, and connection state on foreground return.
**Trade-off:** Three text containers (assistant + status + session name) uses 3 of our 8 text-container slots. Fine for v1.
**Alternatives considered:** Use a list container for sessions (overkill for v1; one session at a time is fine). Use image containers (no images in v1). Skip background-state (loses accumulated assistant reply when phone screen turns off — degrades UX significantly; user explicitly requested this).

### D13: Touch input mapping + best-effort foreground activation

**Choice — touch mapping:**
- **Long-press touchpad** (single finger, > 1s hold) → start a new turn (open audio capture, send `audio.start`)
- **Release after long-press** → stop audio capture, send `audio.stop`
- **Double-tap** → send `stop` frame (interrupt current agent turn)
- **Scroll down** → next session (send `sessions.switch` with `+1`)
- **Scroll up** → previous session (send `sessions.switch` with `-1`)

**Choice — foreground activation:**
When an `assistant.delta` frame arrives and the app determines it is currently backgrounded (tracked via `sysEvent.eventType === OsEventTypeList.FOREGROUND_EXIT_EVENT`), the app attempts `await bridge.callEvenApp('bringToFront')`. This is an undocumented SDK escape hatch (`callEvenApp` per `sdk-reference` is "Low-level direct call to native bridge method. Use when higher-level methods aren't available."). The call is wrapped in try/catch with a no-op fallback — if the Even Hub host doesn't recognize the method, the app silently continues and the user sees the response when they next open the app.

**Rationale — touch:** Matches the SDK's `OsEventTypeList` enum. Long-press-to-talk is the most natural voice UX. Scroll-to-switch-sessions mirrors how Telegram/Discord users switch channels.
**Rationale — foreground:** User explicitly requested "if it is possible to have the app come to foreground on a message response do so." The SDK has no documented foreground API, but `callEvenApp` is the explicit escape hatch for undocumented native methods. Best-effort with graceful fallback is the right shape — we attempt it, and if the host doesn't support it, we degrade silently.
**Alternatives considered:** Tap-to-talk (too easy to trigger accidentally). Button in WebView UI (requires looking at the phone, defeats the purpose of glasses). Skip foreground activation entirely (user explicitly asked for it). Use push notifications (the SDK doesn't expose a notifications API; Even Hub host may not support them for app-originated events).

### D14: QR code generator for fast configuration iteration

**Choice:** Add `plugin/qr_setup.py` that generates a QR code encoding the bridge URL and token. Renders in three forms simultaneously:
1. **Terminal** — prints the QR code as ASCII/Unicode blocks to stdout when the user runs `hermes even-g2 qr`
2. **PNG file** — writes `~/.hermes/even_g2_qr.png` so the user can open it in any image viewer
3. **HTTP endpoint** — serves the PNG at `GET /qr` on the WS server port (e.g., `http://127.0.0.1:8767/qr` or via Tailscale Serve at `https://<magic-dns>:8443/qr`) so the user can scan it directly from the phone browser without saving a file

The QR payload is a query-string URL: `wss://<host>:<port>?token=<token>`. When the glasses-app doesn't yet have stored credentials, it shows a "Scan QR or enter URL manually" screen; a QR scan via the phone camera (or the WebView's experimental barcode APIs if available) populates the URL + token fields.

**Rationale:** User explicitly requested: "I want the QR generator first so that I can do local testing and give feedback without having to sideload the app a billion times." Once the app is installed once, configuration changes (different bridge URL, different token, etc.) can be made by scanning a new QR. No rebuild + reinstall cycle needed.
**Trade-off:** Adds `qrcode` Python dep (~50KB pure-Python). Adds ~80 lines of code for the generator + HTTP endpoint.
**Alternatives considered:** Manual URL + token entry only (works but tedious for iteration). Deep-link `hermes-even-g2://` URL scheme (requires Even Hub to register the scheme — unlikely for a third-party app). Bluetooth beacon for auto-discovery (overkill).

### D15: Session names rendered in a bounded/scrolling text container

**Choice:** The glasses-app renders the active session's name in a dedicated third text container (separate from the assistant reply and status line). Session names come from Hermes Gateway session metadata (if available) or fall back to truncated session IDs (first 16 chars). Long names (> 24 chars at the rendered font size) scroll horizontally in place or truncate with an ellipsis `…`.

**Rationale:** User explicitly requested: "It should show session names (bounded or scrolling characters)." Showing the active session name gives the user context for which conversation they're in when they scroll between sessions via D13's scroll-to-switch.
**Trade-off:** One additional text container (3 of 8 slots used). Minor — leaves 5 slots for future features.
**Alternatives considered:** Show session name only during the scroll-switch interaction, then hide (UX decision — persistent display is more informative and uses a slot we don't need yet). Show full session ID (worse readability).

### D16: Background state persistence via setBackgroundState / onBackgroundRestore

**Choice:** Register state exporters at module init time (per the `background-state` skill's "must run at module init" rule):

```typescript
// State to persist
let accumulatedAssistantText = ''
let currentSessionId: string | null = null
let connectionState: 'disconnected' | 'connecting' | 'connected' = 'disconnected'
let lastTranscript = ''

setBackgroundState('glassesAppState', () => ({
  accumulatedAssistantText,
  currentSessionId,
  connectionState,
  lastTranscript,
}))

onBackgroundRestore('glassesAppState', (saved) => {
  const s = saved as {
    accumulatedAssistantText?: string
    currentSessionId?: string | null
    connectionState?: string
    lastTranscript?: string
  }
  accumulatedAssistantText = s.accumulatedAssistantText ?? accumulatedAssistantText
  currentSessionId = s.currentSessionId ?? currentSessionId
  connectionState = s.connectionState ?? connectionState
  lastTranscript = s.lastTranscript ?? lastTranscript
  // Re-render the HUD from restored state
  renderAccumulatedText()
  renderSessionName()
  // WS will be re-established by the connect() retry loop on foreground return
})
```

**Rationale:** User explicitly requested: "You absolutely should include setBackgroundState/onBackgroundRestore for persistence." Per the `background-state` skill: the Even Hub host migrates the WebView to a headless instance when the phone goes to background, then restores state on foreground return. Without `setBackgroundState`, all JS state is lost — accumulated assistant reply, current session, connection state. With it, the user sees the same HUD state when they return to the app.
**Critical detail:** The WS connection itself is NOT serializable. It will be killed during the headless migration. The glasses-app's `connect()` retry loop (with exponential backoff) handles reconnection on foreground return. On reconnect, the app sends `hello` with the same token + device serial; the plugin's stale-connection guard (per the huntsyea reference) handles the reconnect cleanly.
**Trade-off:** If a streaming response was in flight when the phone went to background, the stream is interrupted. On reconnect, the plugin may have already finalized the response via `edit_message(finalize=True)` — the glasses-app re-renders from `accumulatedAssistantText` (the last delta received before background). The user might miss tokens generated during the background window. Acceptable trade-off for v1; a future enhancement could request a full re-sync from the plugin on reconnect.
**Alternatives considered:** Skip background state (user explicitly rejected — loses the partial assistant reply mid-stream). Persist state via browser localStorage (unreliable in Flutter WebView per device-features skill). Persist via `setLocalStorage` (works but is async; `setBackgroundState` is synchronous from the host's perspective — better for snapshot consistency).

## Risks / Trade-offs

- **[Risk] Hermes plugin install fails on user's gateway version** → **Mitigation:** Pin Hermes Gateway min version in plugin metadata. Document required version in README. Provide a manual install path (`git clone` + symlink into Hermes plugin dir) as fallback.
- **[Risk] Tailscale Serve not available or not configured on gateway host** → **Mitigation:** Detect Tailscale at setup time; if absent, print clear instructions for setting up a reverse proxy and setting `EVEN_G2_BRIDGE_PUBLIC_URL` explicitly. Do NOT fail — just skip the Tailscale step. The plugin works fine behind any reverse proxy.
- **[Risk] parakeet Swift sidecar unavailable on Linux gateway** → **Mitigation:** Auto-fallback to whisper-tiny CPU. Document performance expectations on Linux vs macOS. Mark the parakeet path as optional in env config.
- **[Risk] Rewritten glasses-app SDK compatibility (0.0.12 features)** → **Mitigation:** Pin `@evenrealities/even_hub_sdk@^0.0.12` in package.json. Test against 0.0.12 specifically. If 0.0.13+ breaks anything, document the working version in README.
- **[Risk] WS protocol drift between plugin and glasses-app** → **Mitigation:** D10's generated `protocol.ts` catches drift at build time. CI should run the generator and fail if generated file doesn't match committed file.
- **[Risk] Session routing collision if two glasses pairs connect** → **Mitigation:** D6 uses device serial as chat_id; each pair gets its own session. Documented limitation: doesn't handle the case of two users with the same glasses model on the same tailnet without distinct serials (extremely unlikely).
- **[Risk] Streaming cursor " ▉" leaks into HUD text** → **Mitigation:** D4 explicitly strips it before diffing, matching huntsyea's pattern. Add a test for this case.
- **[Risk] First deployment confusing — user has to install plugin AND glasses-app** → **Mitigation:** README documents the install sequence step-by-step. The BYOA path still works during the transition, so the user isn't blocked if v2 setup hits snags.
- **[Trade-off] Plugin runs only on the Hermes Gateway host** — Can't deploy it elsewhere like we can with bridge-server/. Acceptable for the user's setup; documented as a constraint.
- **[Trade-off] Voice ASR on Linux is slower (~2-5s vs ~100ms on macOS ANE)** — User experience differs by gateway host. Documented in README. User can choose to run gateway on macOS for best UX.
- **[Trade-off] Two text containers (assistant + status) instead of richer UI** — v1 simplicity. A future change can add list containers for sessions, image containers for visual status, etc.

## Migration Plan

This is additive — both paths coexist during rollout.

1. **Build phase** (this change):
   - Implement `plugin/` Python package
   - Rewrite `glasses-app/` TypeScript app (delete old, write new)
   - Smoke-test plugin locally with a fake WS client (no glasses required)
   - Smoke-test rewritten glasses-app against the running plugin (loopback)
2. **First real-hardware test:**
   - User installs plugin on `hermes.local`: `hermes plugins install ./plugin/ && hermes gateway restart`
   - User runs `hermes even-g2 setup` to configure Tailscale Serve and generate the bridge token
   - User packages and installs the rebuilt `glasses-app/` on their phone
   - User opens glasses-app, configures it with the bridge URL + token (or scans a QR code if we add one)
   - User long-presses touchpad, says "hello", observes streaming reply on HUD
3. **Parallel operation:**
   - BYOA bridge-server continues running
   - User can switch between BYOA Add Agent entry and the rewritten glasses-app at will
4. **Eventual cleanup** (separate future change):
   - Once the rewritten glasses-app is stable for the user's typical use, delete `bridge-server/`
   - Optional: also delete `probe/` (no longer needed)

**Rollback:** Disable the plugin (`hermes plugins disable even_g2`) and use the BYOA bridge-server path. No code revert needed if both paths are still deployed.

## Open Questions

These are NOT blocking — the design works with reasonable answers to each. But they're worth noting:

1. **Does Hermes Gateway support the `edit_message(finalize=True)` call pattern cleanly?** — We're inheriting the Telegram/Discord streaming model. Should verify with a quick test against the user's gateway before assuming it works for `even_g2`.
2. **User's `home.arpa` LiteLLM ROCm endpoint** — confirmed the user has `whisper` accessible via LiteLLM. Need to verify the exact model name and that `/v1/audio/transcriptions` accepts WAV-bytes input (standard OpenAI shape, but worth confirming).
3. **Does `bridge.callEvenApp('bringToFront')` actually activate the app on the user's Even Hub version?** — Undocumented escape hatch. Will be tested during hardware validation; if it doesn't work, the foreground activation silently no-ops.
4. **Should sessions.list show Hermes-side session names or just IDs?** — Decision: **show names** (resolved during this design pass; see D15). If Hermes session metadata lacks a name field, fall back to truncated session ID.
5. **How does the rewritten glasses-app handle foreground/background transitions?** — Resolved during this design pass: `setBackgroundState` / `onBackgroundRestore` are required and specified (see D16). The WS connection will need to be re-established on foreground return; the plugin tolerates reconnects from the same chat_id (stale-connection guard per reference impl).

None of these block writing the proposal. They're spike-style questions for the implementation phase.
