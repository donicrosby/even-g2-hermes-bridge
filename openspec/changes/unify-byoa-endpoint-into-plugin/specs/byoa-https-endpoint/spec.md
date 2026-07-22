## ADDED Requirements

### Requirement: BYOA HTTPS endpoint accepts OpenAI/OpenClaw chat-completion requests

The plugin SHALL expose an HTTP `POST` endpoint at `/v1/chat/completions` on the same port as the WS server (default 8767), served via the existing `BridgeServer.process_request` multiplexing hook. The endpoint SHALL accept requests with `Content-Type: application/json` and a body matching the OpenAI chat-completion shape: `{"model": "<string>", "messages": [{"role": "user", "content": "<string>"}]}`. The endpoint SHALL be invocable as the Even Hub "Add Agent" backend — Even's on-device ASR transcribes speech to text, then POSTs the transcribed text to this endpoint.

#### Scenario: Valid BYOA request with fast LLM response
- **WHEN** Even's Add Agent POSTs to `/v1/chat/completions` with a valid bearer token and body `{"model": "openclaw", "messages": [{"role": "user", "content": "What's the weather?"}]}`
- **AND** the LLM responds within the first-delta latency threshold (no `assistant_delta` frames get pushed to the G2 app)
- **THEN** the plugin SHALL return HTTP 200 with `Content-Type: application/json` and an OpenAI chat-completion body
- **AND** the G2 app SHALL NOT have surfaced (no `bringToFront` fired)
- **AND** the Even overlay SHALL display the chat-completion's `choices[0].message.content`

#### Scenario: Valid BYOA request with slow LLM response
- **WHEN** Even's Add Agent POSTs to `/v1/chat/completions` with a valid bearer token and user message
- **AND** the LLM takes longer than the first-delta latency threshold (at least one `assistant_delta` frame is pushed to a connected G2 app)
- **THEN** the G2 app SHALL surface via `bridge.callEvenApp('bringToFront')` (existing `maybeBringToFront` logic)
- **AND** the user SHALL see the streaming response in the G2 app
- **AND** the plugin SHALL still return the full chat-completion JSON when the LLM finishes (the Even overlay receives it late but correctly)

#### Scenario: Missing user message in request body
- **WHEN** the body has no message with `role: "user"` in the `messages` array
- **THEN** the plugin SHALL return HTTP 400 with an OpenAI-style error body `{"error": {"type": "invalid_request_error", "message": "..."}}`
- **AND** SHALL NOT call the LLM or push any frame to the G2 app

#### Scenario: Unsupported HTTP method
- **WHEN** a client sends `GET` to `/v1/chat/completions`
- **THEN** the plugin SHALL return HTTP 405 method-not-allowed
- **AND** SHALL NOT call the LLM

#### Scenario: BYOA_TOKEN not configured
- **WHEN** `BYOA_TOKEN` is unset in `BridgeConfig` and any POST hits `/v1/chat/completions`
- **THEN** the plugin SHALL return HTTP 503 service-unavailable
- **AND** SHALL log a warning that the BYOA endpoint is disabled

### Requirement: Bearer token authentication for BYOA endpoint

The BYOA endpoint SHALL require `Authorization: Bearer <token>` and SHALL accept only requests whose token constant-time-matches the `BYOA_TOKEN` environment variable. The token SHALL be separate from the WS-handshake `EVEN_G2_BRIDGE_TOKEN` so credential rotation is independent per surface. The unauthenticated `/health` endpoint SHALL NOT require either token.

#### Scenario: Valid BYOA token
- **WHEN** `BYOA_TOKEN=secret-byoa-token` and the request sends `Authorization: Bearer secret-byoa-token`
- **THEN** the plugin processes the request normally

#### Scenario: Missing token
- **WHEN** the request omits the `Authorization` header
- **THEN** the plugin returns HTTP 401 with an OpenAI-style error body whose `error.type` is `"auth_error"`
- **AND** SHALL NOT call the LLM or push any frame to the G2 app

#### Scenario: Wrong token
- **WHEN** `BYOA_TOKEN=secret-byoa-token` and the request sends `Authorization: Bearer wrong-token`
- **THEN** the plugin returns HTTP 401 with an OpenAI-style error body whose `error.type` is `"auth_error"`
- **AND** SHALL NOT call the LLM or push any frame to the G2 app

#### Scenario: Token check uses constant-time comparison
- **WHEN** any token comparison is performed
- **THEN** the plugin SHALL use `hmac.compare_digest` (or equivalent) to prevent timing-side-channel attacks

### Requirement: OpenAI chat-completion response shape

The plugin SHALL return successful responses in OpenAI chat-completion JSON shape: `id`, `object: "chat.completion"`, `created`, `model`, `choices[0].message.role: "assistant"`, `choices[0].message.content`, `choices[0].finish_reason: "stop"`, and `usage` fields. The plugin SHALL NOT stream the HTTPS response (no SSE, no chunked transfer) — the entire response SHALL be a single JSON blob returned when the LLM finishes.

Rationale: the archived `sse-tolerance-spike` definitively ruled out SSE — Even's Dart HTTP client rejects `text/event-stream` responses with "network error" before parsing the body. The streaming UX is instead delivered via the WS surface (the G2 app), which naturally activates for slow responses.

#### Scenario: LiteLLM returns assistant content
- **WHEN** the Hermes Gateway (via LiteLLM) returns a successful chat-completion response with `choices[0].message.content = "Hello!"`
- **THEN** the plugin returns HTTP 200 with a body containing `choices[0].message.content = "Hello!"` in OpenAI chat-completion shape

#### Scenario: LiteLLM returns an upstream error
- **WHEN** the Hermes Gateway returns a non-2xx response or fails to connect
- **THEN** the plugin returns HTTP 502 with an OpenAI-style error body whose `error.type` is `"upstream_error"`
- **AND** SHALL log the upstream error with structured fields (chat_id, status_code, error)

### Requirement: BYOA turns tagged with origin for telemetry

The plugin SHALL tag every turn originated by the BYOA HTTPS endpoint with `origin="byoa"` when calling `EvenG2Adapter.send_message` / `edit_message`. Turns originated by the WS surface (the G2 app) SHALL be tagged with `origin="glasses-app"` (the default). The origin tag SHALL be recorded in structured logs (per the existing `connection-debugging` spec) but SHALL NOT change the WS wire format, the frame types pushed, or the `StreamState` computation.

#### Scenario: BYOA request logs its origin
- **WHEN** a BYOA HTTPS turn creates a new session and pushes frames to the G2 app
- **THEN** the plugin's structured logs SHALL include `origin: "byoa"` on the relevant `frame` entries
- **AND** the WS wire format SHALL be identical to a `glasses-app`-originated turn

#### Scenario: WS-originated turn logs its origin
- **WHEN** the G2 app sends a `text` frame that creates a new turn
- **THEN** the plugin's structured logs SHALL include `origin: "glasses-app"` on the relevant `frame` entries
- **AND** the existing behavior SHALL be unchanged

### Requirement: BYOA requests create or reuse a Hermes session

On the first BYOA request from a given device, the plugin SHALL create a new Hermes session via `EvenG2Adapter` and push an `active(session_id, name)` frame to any connected G2 app. On subsequent BYOA requests, the plugin SHALL reuse the existing session (matching by chat_id derived from request context). The G2 app's existing session-list rendering picks up the new session automatically when it next sends `sessions.list`.

#### Scenario: First BYOA request creates a session
- **WHEN** the plugin receives a BYOA request and no Hermes session exists for the request's chat_id
- **THEN** the plugin SHALL create a new Hermes session
- **AND** SHALL push an `active(session_id, name)` frame to any connected G2 app
- **AND** SHALL forward the user's transcribed text to the LLM via the new session

#### Scenario: Subsequent BYOA request reuses the session
- **WHEN** the plugin receives a BYOA request and a Hermes session already exists for the request's chat_id
- **THEN** the plugin SHALL forward the user's transcribed text to the LLM via the existing session
- **AND** SHALL NOT push a new `active` frame (the G2 app already has it)

#### Scenario: G2 app not connected at BYOA request time
- **WHEN** the plugin receives a BYOA request and no G2 app is currently connected via WS
- **THEN** the plugin SHALL still create/reuse the Hermes session and forward to the LLM
- **AND** SHALL return the chat-completion JSON to the BYOA request normally
- **AND** SHALL queue the `active` frame so it can be delivered when the G2 app later connects

### Requirement: Surface handoff is driven by LLM latency, not explicit timer

The plugin SHALL NOT use an explicit timer to decide whether to surface the G2 app. The handoff SHALL be implicit: if the LLM streams at least one `assistant_delta` frame before finishing (i.e., the response is slow enough that streaming would have started), the G2 app's existing `maybeBringToFront` logic activates it. If the LLM finishes fast enough that no `assistant_delta` was ever pushed, the G2 app stays asleep and the Even overlay is the sole surface.

#### Scenario: Fast response does not surface the G2 app
- **WHEN** the LLM returns a complete response before the plugin would have pushed any `assistant_delta` frame
- **THEN** no `assistant_delta` is pushed to the G2 app
- **AND** `maybeBringToFront` does not fire
- **AND** the Even overlay shows the response

#### Scenario: Slow response surfaces the G2 app via existing logic
- **WHEN** the LLM streams at least one chunk of response text
- **THEN** the plugin pushes at least one `assistant_delta` frame to the connected G2 app
- **AND** the G2 app's existing `maybeBringToFront` logic (triggered by `assistant_delta` arriving while backgrounded) activates the G2 app
- **AND** the user sees the streaming response in the G2 app

#### Scenario: G2 app not backgrounded (already in foreground)
- **WHEN** the LLM streams response text and the G2 app is already in the foreground
- **THEN** the existing `maybeBringToFront` no-ops (its `if (!backgrounded) return` early-exit fires)
- **AND** the user continues to see the G2 app (no surface change)

### Requirement: Gzip-compressed responses supported

The plugin SHALL support gzip-compressed HTTP responses when the client sends `Accept-Encoding: gzip`. This preserves compatibility with the existing BYOA contract — the archived `byoa-probe-spike` showed Even's Add Agent client sends `accept-encoding: gzip` by default.

#### Scenario: Client accepts gzip
- **WHEN** the request sends `accept-encoding: gzip` and the response body exceeds the gzip threshold
- **THEN** the plugin returns a valid gzip-compressed response with `Content-Encoding: gzip`

#### Scenario: Client does not accept gzip
- **WHEN** a client omits `Accept-Encoding: gzip`
- **THEN** the plugin returns an uncompressed JSON response

## MODIFIED Requirements

### Requirement: BYOA endpoint served by the plugin, not bridge-server

The `byoa-endpoint` capability's contract (OpenAI/OpenClaw shape, bearer auth, gzip, error responses) SHALL be served by `plugin/` on port 8767 via the existing `process_request` HTTP multiplexer hook. The legacy `bridge-server/` SHALL continue to serve the same contract as a fallback during the transition period but SHALL be marked deprecated in its README. No spec changes to the contract itself — the OpenAI chat-completion shape is preserved exactly.

Rationale: serving both agent surfaces (WS for the custom G2 app, HTTPS for Even Add Agent) in one process enables the latency-driven handoff described in the new `byoa-https-endpoint` capability without requiring an internal side-channel between two separate processes.

#### Scenario: User points Even Add Agent at the plugin
- **WHEN** the user configures Even's Add Agent UI to point at `https://<plugin-host>:<port>/v1/chat/completions` with `BYOA_TOKEN`
- **THEN** the plugin's new POST handler SHALL accept the request and respond in the same OpenAI chat-completion shape that `bridge-server/` produces today

#### Scenario: User keeps using bridge-server during transition
- **WHEN** the user has not yet migrated their Even Add Agent configuration to the plugin's endpoint
- **THEN** `bridge-server/` SHALL continue to function unchanged
- **AND** the plugin's new endpoint SHALL coexist peacefully on its own port
