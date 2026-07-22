## Context

The plugin's `BridgeServer` (in `plugin/src/byoa_plugin/server.py`) uses `websockets.asyncio.server` and intercepts HTTP requests via the `process_request` hook *before* the WS upgrade handshake. That hook is how the existing `/health` and `/qr` endpoints are served on the same port as the WS server (8767). It already supports any HTTP method — we just need to wire a POST handler.

The plugin also already has:
- `EvenG2Adapter` (subclass of `BasePlatformAdapter`) with `send_message` / `edit_message` / `get_chat_info` — the same code path a WS-originated turn uses
- `ConnectionRegistry` mapping `chat_id → websocket connection` for any G2 app that happens to be connected
- `StreamState` for delta computation
- `bridge.callEvenApp('bringToFront')` capability — already wired in `glasses-app/src/main.ts:356`, fires when an `assistant.delta` frame arrives while the G2 app is backgrounded

The legacy `bridge-server/` already implements the OpenAI/OpenClaw BYOA contract — request shape, bearer auth, gzip, OpenAI-style error objects, response formatting. We're porting that logic into the plugin's existing HTTP multiplexer.

### Even's BYOA traffic shape (from the archived `byoa-probe-spike` observations)

What hits the endpoint:
- `POST /` (or `POST /v1/chat/completions`)
- `Authorization: Bearer <token>`
- `x-openclaw-agent-id: main` (always `main` in v1; reserved for future per-agent routing)
- `Content-Type: application/json`
- Body: `{"model":"openclaw","messages":[{"role":"user","content":"<Even's ASR transcript>"}]}`

What we need to return (fast path):
- HTTP 200
- `Content-Type: application/json`
- Body: OpenAI chat-completion shape — `id`, `object: "chat.completion"`, `created`, `model`, `choices[0].message.{role,content}`, `choices[0].finish_reason: "stop"`, `usage`

## Goals / Non-Goals

**Goals:**
- One process, one port, both surfaces (WS + HTTPS). No new service, no internal side-channel.
- Short queries stay in the Even overlay path (audio never leaves the device — Even's ASR handles it).
- Slow queries hand off to the G2 app via the existing streaming + `bringToFront` plumbing — no new "wake up the G2 app" mechanism required.
- OpenAI/OpenClaw wire shape preserved exactly. Even's Add Agent client sees the same response it sees today from `bridge-server/`.
- `bridge-server/` remains alive but deprecated; users can fall back to it during the transition.

**Non-Goals:**
- Removing `bridge-server/` in this change. Follow-up once the new endpoint is validated in production.
- Adding streaming to the BYOA HTTPS response itself. The archived `sse-tolerance-spike` definitively ruled out SSE — Even's Dart HTTP client rejects `text/event-stream`. The HTTPS response remains a single chat-completion JSON blob returned when the LLM finishes.
- Adding new WS frame types. The existing `active` + `assistant_delta` frames already do everything the BYOA slow path needs.
- Modifying the glasses-app. The existing `maybeBringToFront` logic activates the G2 app when `assistant.delta` arrives while backgrounded — that's the entire surface-switch mechanism.

## Decisions

### D1: BYOA handler lives in `http_endpoints.py`, multiplexed on port 8767

**Choice.** Add the POST handler in the existing `HttpEndpointHandler` class. The `BridgeServer.process_request` hook already routes HTTP methods there. The new handler key is `("POST", "/v1/chat/completions")` (or `("POST", "/")` — Even accepts both paths; `/v1/chat/completions` is more conventional).

**Rationale.** Zero new infrastructure. The plugin already multiplexes `/health` and `/qr` on the same port via the same mechanism. TLS termination (Tailscale Serve) is already in place. Users have one URL to remember: `https://hermes.your-tailnet.ts.net:8443/v1/chat/completions`.

**Alternatives considered.**
- **Separate HTTPS port on the plugin.** More firewall surface, more config, no benefit.
- **New tiny service proxying to the plugin.** Adds a process, adds latency, adds operational surface. Explicitly rejected.
- **Keep `bridge-server/` as a thin proxy.** Two-process architecture with internal HTTP callbacks. Rejected for the reasons in the proposal.

### D2: Two tokens — `EVEN_G2_BRIDGE_TOKEN` (WS) and `BYOA_TOKEN` (HTTPS)

**Choice.** Keep the existing convention. WS handshake uses `EVEN_G2_BRIDGE_TOKEN` (already in `BridgeConfig`). HTTPS handler uses `BYOA_TOKEN` (new field in `BridgeConfig`). Both are constant-time compared via `hmac.compare_digest`.

**Rationale.** Compartmentalization — leak one doesn't grant the other. Matches the existing `bridge-server/` convention (`BYOA_TOKEN` is documented in `bridge-server/.env.example`). One rotation event hits only one surface.

**Alternatives considered.**
- **One shared token.** Simpler config but worse blast radius. Rejected.

### D3: Origin-tag in-flight turns, no new frame types

**Choice.** Add `origin: Literal['glasses-app', 'byoa']` as an optional parameter to `EvenG2Adapter.send_message(chat_id, text, *, origin='glasses-app')`. Default `'glasses-app'` preserves existing call sites. The BYOA handler passes `origin='byoa'`.

The tag is recorded on `StreamState` for telemetry but does NOT change the WS wire format. Both origins produce the same `assistant_delta`, `active`, `tool_start`, etc. frames. The tag only affects:
- Logging (so the debug client / structured logs can show "this turn came from BYOA")
- Telemetry counters (future work — not implemented in this change)

**Rationale.** The G2 app's surface-switch logic (`maybeBringToFront`) doesn't care where the turn came from — it only cares that an `assistant_delta` arrived while backgrounded. The latency self-selects the surface: fast responses finish before the first delta would have been pushed (G2 app stays asleep, Even overlay gets the chat-completion); slow responses stream deltas immediately, which wakes the G2 app via existing plumbing.

**Alternatives considered.**
- **A new `byoa_request_received` lifecycle frame.** Rejected — adds wire surface, adds code on both sides, and the existing `active` + `assistant_delta` frames already do the job.
- **An explicit "you should surface now" signal.** Rejected — `maybeBringToFront` is already the right trigger, and it's keyed on the right condition (delta arrives while backgrounded).

### D4: Latency-driven surface selection (no explicit timer)

**Choice.** No explicit "slow path" timer. The handoff is implicit:

```
T+0    BYOA POST arrives, auth passes
T+1    adapter.send_message(chat_id, transcript, origin='byoa')
         → creates/looks up Hermes session
         → pushes `active(session_id, name)` frame to G2 app (if connected)
         → forwards to LLM
T+?    LLM streams response
         → adapter.edit_message pushes `assistant_delta` frames
         → G2 app's maybeBringToFront fires on the FIRST delta (if backgrounded)
         → G2 app surfaces, displays streaming text
T+end  LLM finishes
         → adapter pushes `turn_done` frame
         → BYOA handler returns assembled chat-completion JSON
```

Two outcomes:
- **Fast response (<first-delta latency)**: G2 app never wakes up; Even overlay shows the full chat-completion when it arrives.
- **Slow response (>first-delta latency)**: G2 app surfaces via `maybeBringToFront`; user reads the streaming response there. Even overlay eventually gets the chat-completion too, but the user is already looking at the G2 app.

**Rationale.** This is the cleanest possible threshold — it's automatic, requires no config knob, and uses the SDK's own latency profile as the discriminator. If the LLM is slow enough to push even one delta before finishing, the G2 app surfaces. If the LLM finishes fast, no delta gets pushed and the overlay is the sole surface.

**Alternatives considered.**
- **Explicit timer (e.g., 5s)**: would create a discontinuity — the G2 app might or might not surface depending on whether the timer fired before the first delta. The implicit version is monotonic: more latency → more likely to surface.
- **Always surface the G2 app on BYOA requests**: rejected — defeats the privacy-first UX where short queries stay in the Even overlay.

### D5: chat_id mapping for BYOA requests

**Choice.** Use a single shared chat_id `"even-add-agent"` for all BYOA requests. The Even BYOA spec does not provide a per-user identifier in the request — only `x-openclaw-agent-id: main` (always `main` in v1). Per-user separation happens at the Hermes Gateway layer (which has its own user identity from the platform adapter registration).

**Rationale.** v1 simplicity. The G2 app sees one "session" stream from BYOA requests, and the Hermes Gateway handles user separation via its own auth. If Even later adds a per-user header, we can extract it then — the chat_id mapping is one function call away from being pluggable.

**Alternatives considered.**
- **Per-request chat_id (UUID)**: would create one Hermes session per BYOA request, fragmenting conversation history. Rejected — BYOA is meant to be conversational.
- **Extract user identity from TLS client cert**: not part of Even's BYOA flow.

### D6: Bridge-server deprecation notice, not removal

**Choice.** Add a "Deprecation notice" section to `bridge-server/README.md` pointing at the plugin's new endpoint. Do not delete any code in this change.

**Rationale.** The user needs a rollback path during the transition. Once the plugin's HTTPS endpoint has been validated in production for some time (next change, not this one), `bridge-server/` can be deleted.

## Risks / Trade-offs

- **[Risk: BYOA endpoint misbehaves under load]** → *Mitigation*: the existing `http_endpoints.py` pattern is well-tested (`/health` and `/qr` run in production today). The new handler is a few hundred lines, fully unit-testable without a real LLM. Bridge-server is still alive as fallback during transition.
- **[Risk: G2 app not connected when "Hey Even" fires]** → *Mitigation*: the `active` frame is queued in the session state; when the G2 app later connects and sends `sessions.list`, the session appears. The BYOA response still works (returns chat-completion to the overlay). The user gets the response in the overlay if the G2 app isn't around.
- **[Risk: Even's overlay handles slow responses in unexpected ways]** → *Mitigation*: the archived `sse-tolerance-spike` showed Even's overlay says "Waiting for Agent response..." while waiting. Our HTTPS handler returns the chat-completion when done; Even shows it. If Even's overlay has a hard timeout (e.g., 60s), the LLM response might still arrive late via the G2 app even if the overlay errors out. Documented in README troubleshooting section.
- **[Risk: Same-port multiplexing conflicts with WS upgrade]** → *Mitigation*: `process_request` returns `None` for non-matching paths, letting the WS upgrade proceed. The new handler keys on `("POST", "/v1/chat/completions")` — it won't intercept WS upgrade requests (which are `GET` with `Upgrade: websocket` header). Existing pattern, well-understood.
- **[Risk: BYOA_TOKEN rotation hits production users]** → *Mitigation*: documented in README. Same rotation story as today's `bridge-server/`.

## Migration Plan

Single-PR change, scoped to `plugin/`.

1. Add `BYOA_TOKEN` to `BridgeConfig.from_env()` — defaults to `None` (handler returns 503 if unset).
2. Add POST handler to `http_endpoints.py` — port the parsing/validation/response-formatting logic from `bridge-server/src/byoa_bridge/`.
3. Add `origin` parameter to `adapter.send_message` and `adapter.edit_message` — default preserves existing behavior.
4. Tests for the new handler (`tests/test_http_endpoints.py`).
5. Update `plugin/README.md` — BYOA setup section.
6. Mark `bridge-server/README.md` deprecated, point at the plugin.
7. Manual smoke test on hardware:
   - Configure Even Add Agent to point at `https://<plugin-host>:<port>/v1/chat/completions` with `BYOA_TOKEN`
   - Say "Hey Even, what's the weather?" — verify fast response shows in overlay
   - Say "Hey Even, write me a 1000-word essay on tulips" — verify G2 app surfaces mid-stream with the response

8. Rollback strategy: revert the PR. The plugin reverts to WS-only. `bridge-server/` is still alive (just deprecated). No data migration.

## Open Questions

- **Q1**: Does Even's overlay handle "the G2 app surfaced mid-request" gracefully (i.e., does the overlay close itself when the G2 app takes foreground)? If not, the user might see two surfaces showing the same response, which is confusing. *Needs a hardware probe — could be a small follow-up spike if the behavior is wrong.*
- **Q2**: Should the BYOA handler expose its own `/health` endpoint or rely on the existing one? Current answer: rely on the existing `/health` — it already reports plugin status and the new HTTPS handler is part of the same process.

Both are answerable during the manual smoke test (task 7 in the Migration Plan above); no design decision needed upfront.
