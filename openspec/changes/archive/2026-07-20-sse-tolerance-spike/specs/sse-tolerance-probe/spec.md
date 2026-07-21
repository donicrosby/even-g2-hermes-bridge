## ADDED Requirements

### Requirement: SSE probe server returns text/event-stream on POST

The probe server SHALL accept HTTP POST requests at `/`, `/openresponses`, `/openai-chunk`, and `/raw`, and SHALL always respond with `Content-Type: text/event-stream` and a body of Server-Sent Events matching the route's flavor. The server SHALL NOT return JSON chat-completion bodies on any POST route. Auth SHALL be accepted-but-logged (Bearer header read and recorded, never rejected) so the probe can observe what the glasses actually send.

#### Scenario: Glasses POST to root

- **WHEN** the G2 glasses POST to `/` with `Content-Type: application/json`, `Authorization: Bearer <any-token>`, and body `{"model":"openclaw","messages":[{"role":"user","content":"..."}]}`
- **THEN** the probe responds with `Content-Type: text/event-stream` and emits the OpenResponses event sequence (response.created → response.in_progress → response.output_text.delta → response.completed → [DONE])

#### Scenario: Glasses POST to /openai-chunk flavor

- **WHEN** the glasses POST to `/openai-chunk`
- **THEN** the probe responds with `Content-Type: text/event-stream` and emits OpenAI chat.completion.chunk SSE data lines (`data: {"choices":[{"delta":{"content":"..."}}]}`)

#### Scenario: Glasses POST to /raw flavor

- **WHEN** the glasses POST to `/raw`
- **THEN** the probe responds with `Content-Type: text/event-stream` and emits raw `data: <text>` lines with no event-type prefix

### Requirement: Scripted event timing with env-var delays

The probe server SHALL emit events with configurable delays between them, read from environment variables: `SSE_DELAY_CREATED` (default 0s, delay before response.created), `SSE_DELAY_IN_PROGRESS` (default 2s, delay before response.in_progress), `SSE_DELAY_FIRST_DELTA` (default 3s, delay before first response.output_text.delta), `SSE_DELAY_BETWEEN_DELTAS` (default 0.5s, delay between successive deltas), `SSE_DELAY_BEFORE_COMPLETED` (default 0.5s, delay before response.completed). The server SHALL log the actual delay used for each event so the user can correlate HUD behavior with timing.

#### Scenario: Default timing (Scenario A — normal)

- **WHEN** the probe runs with no env overrides
- **THEN** the total time from POST receipt to `[DONE]` is approximately 6–7 seconds, with a 3-second gap between response.in_progress and the first delta

#### Scenario: Stress timing (Scenario B — long silence)

- **WHEN** the user sets `SSE_DELAY_FIRST_DELTA=35` and restarts the probe
- **THEN** the probe emits response.in_progress immediately, waits 35 seconds, then emits the first response.output_text.delta

### Requirement: Two named launch scenarios

The probe SHALL support two named scenario presets invokable via distinct commands or env-var presets documented in `probe/README.md`: "normal" (default timings, ~7s total) and "stress" (35s first-delta delay). Both scenarios SHALL use the same event shape and the same canned content ("Hello, world. Testing SSE.").

#### Scenario: User runs the normal preset

- **WHEN** the user follows the README's "Scenario A — normal" command
- **THEN** the probe starts with default delays and the user can issue a single utterance to observe baseline SSE behavior

#### Scenario: User runs the stress preset

- **WHEN** the user follows the README's "Scenario B — stress" command
- **THEN** the probe starts with `SSE_DELAY_FIRST_DELTA=35` and the user can issue a single utterance to observe whether the HUD survives a 35s gap

### Requirement: Canned content (no LiteLLM dependency)

The probe server SHALL emit a fixed, scripted response ("Hello, world. Testing SSE.") across all three flavors and both scenarios. The probe SHALL NOT call LiteLLM, Hermes Gateway, or any external LLM. The probe SHALL still accept and log the full request body (headers verbatim, parsed messages) for parity with the byoa-probe-spike's logging.

#### Scenario: Authenticated POST with any user content

- **WHEN** the glasses POST any user content (e.g., "what's the weather?")
- **THEN** the probe ignores the user content and emits the canned "Hello, world. Testing SSE." response

### Requirement: TLS required at startup

The probe server SHALL refuse to start unless `SSL_CERT_FILE` and `SSL_KEY_FILE` env vars are set to existing readable paths. There SHALL be no plaintext fallback — HTTPS is mandatory for parity with prior probes and because Even Hub may reject plaintext.

#### Scenario: Missing SSL env vars

- **WHEN** either `SSL_CERT_FILE` or `SSL_KEY_FILE` is unset or points to a non-existent file
- **THEN** the probe prints a clear error and exits without listening

#### Scenario: SSL env vars valid

- **WHEN** both env vars are set to readable paths
- **THEN** the probe starts and uvicorn reports it is listening on `https://`

### Requirement: Health endpoint does not emit SSE

The probe SHALL expose `GET /health` returning HTTP 200 with a plain JSON body (`{"status":"ok","mode":"sse-probe"}`) so the user can verify the server is up without triggering an SSE response. The health endpoint SHALL NOT require Bearer auth.

#### Scenario: Health check before probe

- **WHEN** the user curls `GET /health` before pointing glasses at the probe
- **THEN** the response is `{"status":"ok","mode":"sse-probe"}` and no events are emitted

### Requirement: Probe is isolated from production code

The SSE probe SHALL be fully contained in `probe/sse_server.py` (plus the existing `probe/pyproject.toml`, `.env.example`, `.venv`). The probe SHALL NOT import from or modify `bridge-server/`, `glasses-app/`, `docker-compose.yml`, `app.json`, or any production file.

#### Scenario: Cleanup after the spike

- **WHEN** the user deletes `probe/sse_server.py` after the spike
- **THEN** no other file in the repository is affected and `git status` shows only the deletion of that one file (plus the change directory under `openspec/changes/sse-tolerance-spike/`)
