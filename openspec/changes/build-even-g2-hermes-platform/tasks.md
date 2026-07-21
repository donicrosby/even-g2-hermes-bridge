## 1. Plugin package scaffold

- [x] 1.1 Create `plugin/pyproject.toml` with `[project]` metadata, dependencies (`websockets>=16.0`, `numpy>=1.26`, `faster-whisper>=1.2.1`, `qrcode>=7.4`, `httpx>=0.27.0`), and `[build-system]` using `uv_build>=0.11.25,<0.12` per `AGENTS.md`
- [x] 1.2 Create `plugin/src/byoa_plugin/__init__.py` with `register(ctx)` entry point calling `ctx.register_platform(...)` for `even_g2`
- [x] 1.3 Create `plugin/src/byoa_plugin/config.py` reading all env vars: `EVEN_G2_BRIDGE_TOKEN`, `EVEN_G2_BRIDGE_HOST`, `EVEN_G2_BRIDGE_PORT`, `EVEN_G2_BRIDGE_PUBLIC_URL`, `EVEN_G2_HOME_CHANNEL`, `EVEN_G2_ALLOWED_USERS`, `EVEN_G2_ALLOW_ALL_USERS`, `EVEN_G2_ASR_LITELLM_MODEL`, `EVEN_G2_ASR_LITELLM_BASE_URL`, `EVEN_G2_ASR_LITELLM_API_KEY`, `EVEN_G2_ASR_SIDECAR_BIN`, `EVEN_G2_ASR_STATE`
- [x] 1.4 Run `uv sync` inside `plugin/` to generate `uv.lock` and verify the package imports
- [x] 1.5 Create `plugin/.env.example` documenting all env vars with defaults

## 2. WebSocket protocol module

- [x] 2.1 Create `plugin/src/byoa_plugin/protocol.py` defining all frame schemas as `TypedDict` or `dataclass`: `hello`, `text`, `audio.start`, `audio.stop`, `sessions.list`, `sessions.switch`, `sessions.new`, `stop`, `assistant.delta`, `assistant`, `tool.start`, `tool.end`, `turn.done`, `sessions`, `active`, `history`, `transcript`, `error`
- [x] 2.2 Implement frame constructors: `P.hello(token, device)`, `P.assistant_delta(text)`, `P.tool_start(name, label, emoji)`, `P.tool_end(name, ok)`, `P.turn_done()`, `P.transcript(text)`, `P.error(message)`, `P.sessions(items, active)`, `P.active(session_id, name)`
- [x] 2.3 Implement frame parser: `P.parse_client(raw_or_bytes)` → dict with `t` field; reject malformed frames with a clear error
- [x] 2.4 Add a `protocol_gen.py` script that emits `glasses-app/src/protocol.ts` from `protocol.py` (TypedDict → TypeScript interface, constants → TS constants)
- [x] 2.5 Commit the generated `glasses-app/src/protocol.ts` and document the regeneration command in `plugin/README.md`

## 3. Connection registry and StreamState

- [x] 3.1 Create `plugin/src/byoa_plugin/connections.py` with `ConnectionRegistry` class: `register(chat_id, ws)`, `unregister(chat_id)`, `send_frame(chat_id, frame_str)`, `get(chat_id) → ws | None`
- [x] 3.2 Implement `StreamState` dataclass: `sent_len: int`, `delta_for(accumulated: str) → str` (strips trailing ` ▉` cursor before diffing)
- [x] 3.3 Implement `STREAMING_CURSOR = " ▉"` constant in `protocol.py` or `connections.py`
- [x] 3.4 Add tests for `StreamState.delta_for`: first-call returns full text, subsequent returns suffix, cursor-stripping, content-shrinks reset

## 4. WebSocket server

- [x] 4.1 Create `plugin/src/byoa_plugin/server.py` with a WS server class that binds to `EVEN_G2_BRIDGE_HOST:EVEN_G2_BRIDGE_PORT` (default `127.0.0.1:8767`)
- [x] 4.2 Implement the connection handler: accept WS, receive first frame, validate `hello` + token via `hmac.compare_digest`, close with code 1008 on auth failure, register `chat_id` on success
- [x] 4.3 Implement the receive loop: parse JSON frames, dispatch by `t` field to handlers (`text`, `audio.start`, `audio.stop`, `sessions.list`, `sessions.switch`, `sessions.new`, `stop`); accept binary frames during audio-capturing state; log and skip unknown frame types
- [x] 4.4 Implement the ping keepalive: every 30s, send a WS protocol-level ping to each connected client
- [x] 4.5 Implement graceful disconnect: unregister chat_id on WS close, cancel any in-flight ASR task for that chat_id

## 5. Hermes platform adapter

- [x] 5.1 Create `plugin/src/byoa_plugin/adapter.py` with `EvenG2Adapter(BasePlatformAdapter)` class
- [x] 5.2 Implement `connect()`: start the WS server, mark connected
- [x] 5.3 Implement `disconnect()`: stop the WS server, close all connections, mark disconnected
- [x] 5.4 Implement `send_message(chat_id, text)`: reset `StreamState` for the chat_id, compute delta, push `assistant.delta` frame, return `SendResult(success=True, message_id="g2")`
- [x] 5.5 Implement `edit_message(chat_id, message_id, text, *, finalize=False)`: compute delta via existing `StreamState`, push `assistant.delta`, on `finalize=True` also push `turn.done`
- [x] 5.6 Implement `get_chat_info(chat_id)`: return `{"name": chat_id, "type": "dm"}` (minimal for v1)
- [x] 5.7 Wire the adapter's message handler: on inbound `text` frame, construct `MessageEvent` and call the gateway's `set_message_handler` callback
- [x] 5.8 Wire the adapter's voice handler: on `audio.stop` + transcript, construct `MessageEvent` of type voice and call the same callback

## 6. Tool-call hooks

- [x] 6.1 Create `plugin/src/byoa_plugin/hooks.py` with `pre_tool_call` and `post_tool_call` hook functions
- [x] 6.2 Implement `pre_tool_call`: look up chat_id by session_id, emit `tool.start` frame with name + label
- [x] 6.3 Implement `post_tool_call`: look up chat_id, emit `tool.end` frame with name + ok status
- [x] 6.4 Register hooks in `register(ctx)` via `ctx.register_hook("pre_tool_call", ...)` and `ctx.register_hook("post_tool_call", ...)`
- [x] 6.5 Implement `tool_label(tool_name, args)` helper that produces a human-friendly label (e.g., "web_search" + args → "Searching the web: <query>")

## 7. Voice ASR (three backends)

- [x] 7.1 Create `plugin/src/byoa_plugin/asr/__init__.py` with a `transcribe(pcm16_bytes) → str` entry point that dispatches to the configured backend in priority order: LiteLLM → parakeet → whisper-tiny
- [x] 7.2 Create `plugin/src/byoa_plugin/asr/litellm.py`: wrap PCM16 bytes as WAV in-memory (16kHz mono, via `wave` + `io.BytesIO`), POST to `{EVEN_G2_ASR_LITELLM_BASE_URL or LITELLM_BASE_URL}/v1/audio/transcriptions` with `Authorization: Bearer {EVEN_G2_ASR_LITELLM_API_KEY or LITELLM_API_KEY}`, multipart form with `file` + `model`; parse JSON response `{"text": "..."}`; on failure, raise `ASRUnavailable` so the dispatcher falls back
- [x] 7.3 Create `plugin/src/byoa_plugin/asr/whisper_fallback.py`: `faster-whisper` wrapper using `whisper-tiny` model on CPU; lazy-load on first call; on failure raise `ASRUnavailable`
- [~] 7.4 Create `plugin/src/byoa_plugin/asr/parakeet.py`: subprocess wrapper around the Swift sidecar; stdin = PCM bytes, stdout = JSON `{"text": "..."}`; lazy-load on first call; verify Developer ID signature before execution
  - **Subprocess wrapper, stdin/stdout JSON, lazy-load all implemented.** Developer ID signature verification is **out of scope** — this deployment is Linux-only and parakeet is macOS-only future path. Won't add macOS-specific code paths.
- [x] 7.5 Implement dispatcher logic in `asr/__init__.py`: if `EVEN_G2_ASR_LITELLM_MODEL` is set, try LiteLLM first; on `ASRUnavailable` fall through to parakeet (if sidecar configured) → whisper-tiny (always available)
- [x] 7.6 Handle empty/silent audio: return empty string without invoking any ASR backend (caller treats empty as "didn't catch that")
- [x] 7.7 Wire ASR into the WS server's `audio.stop` handler: accumulate PCM, call `asr.transcribe(bytes)`, emit `transcript` frame, forward to adapter's voice handler
- [ ] 7.8 Test LiteLLM ASR path against the user's `litellm.local` endpoint with the `whisper` model (or whatever the user has configured)
  - **Blocked on**: requires live network access to the user's LiteLLM instance.

## 8. QR code generator and network setup

- [x] 8.1 Create `plugin/src/byoa_plugin/qr_setup.py` with `generate_qr(url, token) → bytes` that encodes `wss://<url>?token=<token>` as a PNG image via the `qrcode` library
- [x] 8.2 Implement terminal ASCII/Unicode rendering of the QR (so `hermes even-g2 qr` prints the QR directly to the terminal for scanning from a phone camera pointed at the screen)
- [x] 8.3 Implement PNG file output: write to `~/.hermes/even_g2_qr.png` if `--out` is not specified
- [x] 8.4 Implement HTTP endpoint: register `GET /qr` on the WS server that returns the PNG with `Content-Type: image/png` (so the user can open `https://<host>:<port>/qr` in a phone browser and scan from there)
- [x] 8.5 Register a CLI command `hermes even-g2 qr` via `ctx.register_cli_command(...)` in `register(ctx)` that prints QR + writes PNG + prints the URL
- [x] 8.6 Create `plugin/src/byoa_plugin/net.py` with `resolve_advertised_url(cfg) → str` implementing the priority chain: explicit `EVEN_G2_BRIDGE_PUBLIC_URL` → Tailscale MagicDNS via `tailscale status --json` → LAN IP fallback. Include `tailscale_available() → bool` helper
- [x] 8.7 Expose the resolved URL via `GET /health` (returned as `advertised_url` field) so users can verify what URL the QR/CLI will advertise
- [x] 8.8 Create `plugin/src/byoa_plugin/setup_flow.py` with `setup(cfg, force_token=False)`:
  - Generate `EVEN_G2_BRIDGE_TOKEN` if missing (write to `~/.hermes/.env` or print "set this env var")
  - If Tailscale is detected: run `tailscale serve --https=8443 --bg http://127.0.0.1:<port>`, set `EVEN_G2_BRIDGE_PUBLIC_URL` from MagicDNS
  - If Tailscale is NOT detected: print "Tailscale not detected. Set `EVEN_G2_BRIDGE_PUBLIC_URL=wss://your-external-url` and configure your reverse proxy to forward to `http://<this-host>:<port>`." Do NOT fail.
- [x] 8.9 Implement `build_serve_command(cfg, serve_port)` returning the `tailscale serve` argv (only used in the Tailscale path)
- [x] 8.10 Register a CLI command `hermes even-g2 setup` via `ctx.register_cli_command(...)`
- [x] 8.11 Document both deployment patterns in `plugin/README.md`: (a) Tailscale Serve (auto-configured), (b) User-provided reverse proxy (user sets `EVEN_G2_BRIDGE_PUBLIC_URL` explicitly). Include example nginx/caddy configs in an appendix.

## 9. Rewrite glasses-app

- [x] 9.1 Delete the existing `glasses-app/src/main.ts` and `glasses-app/index.html` contents (preserve Vite/TS scaffold: `package.json`, `tsconfig.json`, `vite.config.ts`, `app.json`)
- [x] 9.2 Generate `glasses-app/src/protocol.ts` from `plugin/src/byoa_plugin/protocol.py` via the `protocol_gen.py` script
- [x] 9.3 Implement `glasses-app/src/main.ts`: `waitForEvenAppBridge()` → `createStartUpPageContainer` with **three** text containers (assistant reply + status line + session name) → connect WS → send `hello` with token + device serial → event loop
- [x] 9.4 Implement touch handlers: long-press → `audioControl(true, Glasses)` + send `audio.start` + display "Listening..."; release → `audioControl(false)` + send `audio.stop` + display "Processing..."; double-tap → send `stop` + display "Stopped"; scroll → send `sessions.switch` +1/-1
  - **Note**: SDK has no long-press event — mapped single-press to toggle mic (start/stop), double-press to interrupt.
- [x] 9.5 Implement audio frame streaming: on `audioEvent` from `onEvenHubEvent`, send the PCM bytes as binary WS frame
- [x] 9.6 Implement inbound frame handling: `assistant.delta` → append to accumulated text → `textContainerUpgrade` on assistant container; `tool.start` → render to status container; `tool.end` → clear status container; `transcript` → display "You said: <text>"; `turn.done` → mark reply complete; `error` → display error; `active` → display new session name in session-name container (truncated/scrolling if > 24 chars)
- [x] 9.7 Implement connection lifecycle: exponential backoff on disconnect (but NO retry on auth failure code 1008); display "Disconnected" / "Connecting..." / "Connected" on status container
- [x] 9.8 Implement `bridge.setLocalStorage` / `getLocalStorage` persistence for bridge URL and token
- [x] 9.9 Implement session-name rendering: on `active` frame, extract session name (or fall back to truncated ID), truncate to 24 chars with ellipsis OR scroll horizontally if longer, render to session-name container via `textContainerUpgrade`
- [x] 9.10 Implement background state persistence: register `setBackgroundState('glassesAppState', exporter)` and `onBackgroundRestore('glassesAppState', restorer)` at module init time (before `onEvenHubEvent`); snapshot `accumulatedAssistantText`, `currentSessionId`, `connectionState`, `lastTranscript`; restore with `??` fallbacks; re-render HUD on restore
  - **SDK 0.0.12 lacks `setBackgroundState`/`onBackgroundRestore`** (verified by grepping the published type definitions). Using `setLocalStorage`/`getLocalStorage` instead with debounced save on state changes and a flush on `FOREGROUND_EXIT_EVENT`. Functional behavior matches: state survives background/foreground transitions.
- [x] 9.11 Implement foreground tracking: listen for `sysEvent.eventType === FOREGROUND_EXIT_EVENT` (set backgrounded=true) and `FOREGROUND_ENTER_EVENT` (set backgrounded=false)
- [x] 9.12 Implement best-effort foreground activation: on `assistant.delta` arrival while `backgrounded === true`, call `await bridge.callEvenApp('bringToFront')` inside try/catch; on throw, silently continue (response still renders via headless WebView)
- [x] 9.13 Update `glasses-app/app.json` permissions: declare `g2-microphone`, `phone-microphone` (for fallback), and ensure `network` whitelist includes the Tailscale WSS URL pattern
  - Whitelist broadened to `wss://*.ts.net`, `wss://*.home.arpa`, `wss://*.local`, plus `192.168.*.*` and `10.*.*.*` LAN ranges.
- [x] 9.14 Run `npm install && npm run build` to verify the rewrite compiles
  - SDK upgraded from 0.0.11 → 0.0.12 (latest). Build passes in ~220ms. Output: `dist/index.html` (0.28 kB) + `dist/assets/index-DDPt_nOo.js` (77.82 kB / 30.20 kB gzip).

## 10. Integration tests (no glasses required)

- [x] 10.1 Write a fake WS client script (`plugin/tests/fake_client.py`) that connects, sends `hello` with the right token, sends a `text` frame, and captures the `assistant.delta` frames the plugin would push (using a mocked `EvenG2Adapter`)
- [x] 10.2 Test: invalid token → connection closed with code 1008
- [x] 10.3 Test: `text` frame → adapter receives a `MessageEvent` with the right content
- [x] 10.4 Test: `StreamState.delta_for` correctly produces deltas across multiple `edit_message` calls
- [x] 10.5 Test: unknown frame type → logged and not fatal
- [x] 10.6 Test: parakeet sidecar fallback to whisper when sidecar binary path is invalid
- [x] 10.7 Run `uv run pytest plugin/tests/` and verify all pass

  **Additional coverage added beyond spec**: `test_connections.py` (19 tests for `ConnectionRegistry` race guards), `test_http_endpoints.py` (15 tests for `/health` + `/qr`), `test_config.py` (20 tests for `BridgeConfig.from_env`). These unit tests caught three real bugs: missing `ConnectionClosed` import, missing `reason_phrase` in `Response()`, and a `reset_env` fixture that didn't actually isolate env.

## 11. Plugin install and first-run smoke

- [x] 11.1 Run `uv sync` in `plugin/` to install all deps including dev tools
- [x] 11.2 Run `uv build` to verify wheel + sdist produce cleanly via `uv_build`
- [x] 11.3 Document the `hermes plugins install ./plugin/` install command in `plugin/README.md`
- [x] 11.4 Document the env-var configuration flow in `plugin/README.md` (or refer to `plugin/.env.example`)
- [x] 11.5 Document the `hermes even-g2 setup` CLI command and what it does (generates token, binds loopback, runs Tailscale Serve)

## 12. Real-hardware validation (USER RUNS THIS)

- [ ] 12.1 Install plugin on `hermes.local`: `hermes plugins install ./plugin/ && hermes plugins enable even_g2 && hermes gateway restart`
- [ ] 12.2 Run `hermes even-g2 setup` to configure Tailscale Serve and generate the bridge token
- [ ] 12.3 Package and install the rewritten `glasses-app/` on the phone via Even's app loading flow
- [ ] 12.4 Open the glasses-app, enter the bridge URL + token (or use auto-discovery if implemented)
- [ ] 12.5 Long-press touchpad, say "hello", observe streaming reply on HUD
- [ ] 12.6 Test: long prompt that takes > 30s to respond — verify HUD survives (no timeout, unlike BYOA)
- [ ] 12.7 Test: double-tap to interrupt — verify agent stops and HUD shows "Stopped"
- [ ] 12.8 Test: scroll to switch sessions — verify session list appears and switching works
- [ ] 12.9 Test: tool call (if the agent uses tools) — verify `tool.start` / `tool.end` frames render on status line
- [ ] 12.10 Capture observations in `openspec/changes/build-even-g2-hermes-platform/observations.md`

## 13. Cleanup and handoff

- [ ] 13.1 Verify `git status` shows intended changes: new `plugin/` directory, rewritten `glasses-app/`, updated root `README.md`, and this change directory
- [ ] 13.2 Verify `bridge-server/` is unchanged
- [ ] 13.3 Update root `README.md` with dual-architecture description (BYOA lite path + full WS path)
- [ ] 13.4 Document known limitations in README: plugin requires Hermes Gateway host, macOS recommended for parakeet ASR, single-user for v1
- [ ] 13.5 Recommend follow-up cleanup change to delete `bridge-server/`, `glasses-app/` legacy code, and `probe/` once WS path is stable
