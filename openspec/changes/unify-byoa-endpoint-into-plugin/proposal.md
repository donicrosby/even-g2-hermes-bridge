## Why

Two agent surfaces for the same Hermes backend exist in parallel today:

- **Path A** — `plugin/` exposes a WS server (Protobuf frames) that the custom G2 app talks to. Streaming, tools, sessions, voice via our ASR.
- **Path B** — `bridge-server/` exposes an HTTPS endpoint (OpenAI/OpenClaw shape) that Even's built-in Add Agent UI talks to. Stateful chat-completion responses, Even's on-device ASR for privacy.

Both work, both reach the same Hermes Gateway, neither is being removed. But Path B has a structural problem the user has hit repeatedly: when the LLM takes more than ~30s to respond, Even's overlay shows "Waiting for Agent response..." indefinitely and the late response is lost. The user wants a hybrid where short queries stay in the private Even-overlay path (audio never leaves the device) and slow queries hand off to Path A's G2 app for streaming display.

Today that handoff is impossible because the two surfaces live in separate processes (`bridge-server/` and `plugin/`) with no shared state, no shared connection registry, no shared session map. Coordinating them would require an internal HTTP callback, a shared database, or some other side-channel — all of which add operational surface without adding user value.

The architecturally correct answer is to merge: have the plugin serve *both* the WS endpoint (existing) and the BYOA HTTPS endpoint (new), in one process, on one port, sharing one adapter + one connection registry. Then the handoff is just a function call.

## What Changes

- **Add** an HTTPS `POST /v1/chat/completions` handler to `plugin/src/byoa_plugin/http_endpoints.py`. It accepts the Even Hub "Add Agent" BYOA request shape (OpenAI/OpenClaw chat-completion JSON with `model`, `messages`, optional `x-openclaw-agent-id` header), authenticates via `BYOA_TOKEN`, and forwards to the Hermes Gateway via the existing `EvenG2Adapter`.
- **Add** origin-tagging to in-flight turns so the adapter knows whether a turn came from the WS surface (`glasses-app`) or the HTTPS surface (`byoa`). The tag influences session creation and frame-push behavior but does not change the wire format.
- **Add** `BYOA_TOKEN` as a config env var (separate from `EVEN_G2_BRIDGE_TOKEN`) so the existing BYOA convention is preserved. Both tokens are constant-time compared.
- **Deprecate** `bridge-server/`. Its FastAPI app, Dockerfile, and README are marked legacy. The plugin's new HTTPS endpoint subsumes its role. `bridge-server/` is not deleted in this change; a follow-up change removes it once the new endpoint has been validated on hardware.
- **Update** `plugin/README.md` with BYOA setup instructions (token, Add Agent UI walkthrough, what to expect for fast vs. slow responses).
- **No changes** to the WS protocol, Protobuf schema, glasses-app, or existing G2 app behavior. The new HTTPS endpoint reuses the existing `EvenG2Adapter`, `ConnectionRegistry`, and `StreamState` — the same code paths a WS-originated turn uses.

## Capabilities

### New Capabilities
- `byoa-https-endpoint`: Defines the POST shape, bearer auth, OpenAI chat-completion request/response contract, origin-tagging, session creation/lookup behavior, and the latency-driven surface selection (fast responses stay in the Even overlay, slow responses stream to the G2 app via existing `assistant.delta` + `maybeBringToFront` plumbing).

### Modified Capabilities
- `byoa-endpoint`: Note that the endpoint is now served by the plugin (port 8767 via `process_request` multiplexing) rather than by `bridge-server/`. No spec changes to the contract itself — the OpenAI chat-completion shape is preserved exactly. The `bridge-server/` process becomes optional and eventually removable.

## Impact

**Affected code:**
- `plugin/src/byoa_plugin/http_endpoints.py` — add POST handler + OpenAI request/response parsing + auth
- `plugin/src/byoa_plugin/config.py` — add `BYOA_TOKEN` field
- `plugin/src/byoa_plugin/adapter.py` — accept an optional `origin: Literal['glasses-app', 'byoa']` on `send_message` so the registry can tag the turn
- `plugin/src/byoa_plugin/connections.py` — optional: track origin on `StreamState` for telemetry (no behavior change)
- `plugin/tests/test_http_endpoints.py` (new) — covers POST handler auth, request validation, response shape, session creation
- `plugin/README.md` — BYOA setup section
- `bridge-server/README.md` — deprecation notice pointing at the plugin

**No protocol changes**: the WS frame schema is untouched, the BYOA OpenAI chat-completion shape is untouched. This is a packaging change, not a protocol change.

**Runtime behavior change**:
- The plugin now serves both `WS /` (existing) and `POST /v1/chat/completions` (new) on port 8767
- A BYOA request creates/looks up a Hermes session and pushes an `active` frame to any connected G2 app (prepping the surface)
- The LLM response flows back through the adapter as `assistant.delta` frames — the G2 app's existing `maybeBringToFront` logic activates it if it was backgrounded
- The HTTPS response returns the full chat-completion JSON when the LLM finishes (same as bridge-server did); if the G2 app surfaced, the user sees the response there first, the HTTPS response is "redundant but correct"

**Rollback risk**: low. If the new HTTPS endpoint misbehaves, users can keep using `bridge-server/` (still alive, just deprecated) until the new endpoint is fixed. No data migration, no schema changes.

**Out of scope** (future work, not this change):
- Actually deleting `bridge-server/`. That's a follow-up once the plugin's HTTPS endpoint has been validated in production for some time.
- New BYOA-spec extensions (e.g., partial-response streaming, multi-turn conversation state). The OpenAI/OpenClaw shape is preserved as-is.
- A "force slow path" debug mode. Manual testing can verify the handoff by deliberately slowing the LLM with a debug env var.
- Removing the WS path. Path A and Path B (now served by the plugin) continue to coexist indefinitely — the user picks the surface by which app they invoke.
