## Context

The `bridge-server-byoa-migration` (just shipped) implements a synchronous OpenClaw BYOA endpoint: glasses POST `/`, bridge returns one JSON chat-completion blob. The bridge handles short responses well but has no answer for agents that take 30+ seconds to respond. The glasses' BYOA client times out, the user sees an error, and the agent's late output never reaches the HUD.

OpenClaw's spec (per `openclaw/openclaw` repo docs at `docs/gateway/openresponses-http-api.md`) defines a streaming SSE protocol that solves this cleanly:

```
event: response.created
event: response.in_progress         ← "agent is thinking"
event: response.output_text.delta   ← token-by-token streaming
: ping                              ← keepalive every 30s, resets idle timer
event: response.completed
data: [DONE]
```

If the glasses' built-in Add Agent client consumes `text/event-stream`, the slow-agent problem largely dissolves: the bridge can stream tokens as they arrive, the `: ping` keepalives defeat idle timeouts, and the agent can take minutes if needed.

**The unknown**: does the G2 BYOA client consume SSE? Three signals from the prior probe suggest it might not:
- Glasses sent no `Accept: text/event-stream` header
- Glasses sent no `stream: true` body field
- The dAAAb reference worker (the canonical BYOA implementation) returns non-streaming JSON

But none of these *disprove* SSE tolerance — a well-behaved OpenAI client doesn't have to ask for SSE; the server can return `text/event-stream` unprompted. We need to test it.

Constraints:
- User has working G2 hardware, a working local CA for HTTPS, and Tailscale routing (`your-host.your-tailnet.ts.net`) proven in the prior probe
- Existing `probe/` directory from the byoa-probe-spike (archived) is still present with its `pyproject.toml`, `.venv`, and `.env.example`
- This spike must be fully contained in `probe/` and not pollute production code

## Goals / Non-Goals

**Goals:**
- Stand up a probe server in under an hour that always returns `text/event-stream` on POST `/`, emitting a scripted sequence of OpenResponses-style events with configurable delays
- Resolve the load-bearing unknown: does the G2 BYOA client consume SSE, partially consume it (render but then break), or reject it entirely?
- Capture observations that determine the next architecture decision
- Stay fully isolated from production code so cleanup is `rm probe/sse_server.py`

**Non-Goals:**
- Implement SSE in the production `bridge-server/` — that's a separate change built on this spike's findings
- Implement a full OpenClaw `/v1/responses` endpoint — the probe needs only enough fidelity to test the glasses' SSE tolerance
- Forward to LiteLLM or Hermes Gateway — the probe uses a canned response so we control timing precisely
- Test what happens to SSE across Tailscale proxies — that's a follow-up if SSE itself works
- Test WebSocket tolerance — out of scope; the OpenClaw spec uses SSE for HTTP streaming, and the BYOA client is an HTTP client

## Decisions

### D1: Probe lives in `probe/sse_server.py`, reusing existing `probe/` scaffold

**Choice:** New file `probe/sse_server.py` alongside the existing `byoa_probe/server.py`. Same `pyproject.toml`, same `.venv`, same `.env.example` pattern.
**Rationale:** Zero new package infrastructure. The user already has `probe/.venv` set up from the prior spike. Launching a different uvicorn app from the same venv is one CLI invocation.
**Alternatives considered:** New top-level directory `probe-sse/` (unnecessary isolation — they share all deps); fold into `byoa_probe/server.py` as a mode flag (mixes probe concerns, harder to dispose of this one independently).

### D2: Always return SSE on POST `/`, never return JSON

**Choice:** The probe has a single POST `/` route that always returns `Content-Type: text/event-stream` with a scripted event sequence. No JSON fallback path.
**Rationale:** The probe's sole purpose is to test SSE tolerance. A JSON fallback would let us weasel out of the answer. We need to force the glasses to deal with `text/event-stream` and observe what happens.
**Alternatives considered:** Toggle between JSON and SSE based on body or query param (over-engineered for a one-question spike).

### D3: Scripted event sequence with configurable artificial delays

**Choice:** The probe emits this event sequence on every POST `/`:

```
POST / received
  ↓ (configurable delay, default 0s)
event: response.created
data: {"id":"sse-probe-1","status":"in_progress"}
  ↓ (delay, default 2s)
event: response.in_progress
data: {"id":"sse-probe-1","status":"in_progress"}
  ↓ (delay, default 3s — long enough to see if HUD shows a "thinking" state)
event: response.output_text.delta
data: {"delta":"Hello"}
  ↓ (delay, default 500ms)
event: response.output_text.delta
data: {"delta":", world"}
  ↓ (delay, default 200ms)
event: response.output_text.delta
data: {"delta":". Testing SSE."}
  ↓ (delay, default 500ms)
event: response.completed
data: {"status":"completed"}
  ↓
data: [DONE]
```

Delays are controlled by env vars: `SSE_DELAY_CREATED=0`, `SSE_DELAY_IN_PROGRESS=2`, `SSE_DELAY_FIRST_DELTA=3`, `SSE_DELAY_BETWEEN_DELTAS=0.5`, `SSE_DELAY_BEFORE_COMPLETED=0.5`. This lets the user re-run with different timings without code changes.

**Rationale:** Scripted events give us controlled observations. If the glasses render "Hello, world. Testing SSE.", SSE works. If they render only the first delta, partial. If they show nothing or an error, rejected. The 3-second `FIRST_DELTA` delay specifically tests whether the HUD shows any "thinking" state during the gap between `response.created` and the first token.

**Alternatives considered:** Random delays (less reproducible); forward to real LiteLLM with stream:true (loses timing control, confounds "did the glasses break?" with "did LiteLLM break?").

### D4: Two probe scenarios — normal timing and stress timing

**Choice:** Document two named probe scenarios the user runs sequentially:

**Scenario A — Normal timing (does SSE work at all?):**
```
Delays: created=0, in_progress=2s, first_delta=3s, between_deltas=500ms, before_completed=500ms
Total: ~7s from POST to [DONE]
Tests: does the HUD render the scripted text at all?
       does it render deltas progressively, or wait for [DONE]?
```

**Scenario B — Stress timing (does SSE survive long silence?):**
```
Delays: created=0, in_progress=2s, first_delta=35s, between_deltas=500ms, before_completed=500ms
Total: ~39s from POST to [DONE]
Tests: does the HUD survive a 35s gap between in_progress and first token?
       Does the glasses-side 30s timeout fire, or does the SSE stream keep it alive?
```

Each scenario has its own launcher command (env vars preset).

**Rationale:** Scenario A answers "does SSE work at all?" Scenario B answers "does SSE solve the slow-agent problem?" They're independent questions — A could pass while B fails (e.g., glasses consume SSE but with a 30s total-response timeout, not an idle timeout).
**Alternatives considered:** Single scenario (would conflate the two questions); many scenarios (overkill for a 30-minute spike).

### D5: Three SSE event flavors — OpenResponses, OpenAI chat.completion.chunk, raw

**Choice:** The probe exposes three POST endpoints — `/openresponses`, `/openai-chunk`, `/raw` — each emitting the same scripted timing but with different event shapes:

- **POST `/openresponses`**: `event: response.created`, `event: response.output_text.delta`, `event: response.completed` (the OpenClaw spec format)
- **POST `/openai-chunk`**: `data: {"choices":[{"delta":{"content":"Hello"}}]}` (the OpenAI `/v1/chat/completions` streaming format with `stream: true`)
- **POST `/raw`**: `data: Hello\n\n` (no event type, just raw SSE data lines)

Plus **POST `/`** which defaults to `/openresponses` (the OpenClaw canonical shape).

**Rationale:** We don't know which SSE format the glasses expect (if they consume SSE at all). Testing three flavors in one probe saves three separate spike cycles. If one works and others don't, that tells us about the glasses' SSE parser.
**Alternatives considered:** Only test OpenResponses (assumes the glasses use OpenClaw's full spec — but they only use the minimal POST shape, so they may also use a simpler SSE format).

### D6: Bearer auth accepted-but-logged (same as prior probes)

**Choice:** Read `Authorization` header, log it, never reject.
**Rationale:** Same as the byoa-probe-spike and for the same reason: we want to observe what the glasses send without blocking on wrong auth guesses.
**Alternatives considered:** Enforce a token (would block the probe if we guessed wrong).

### D7: HTTPS-only via user's local CA (same as prior probes)

**Choice:** Same SSL pattern as the prior probes: `SSL_CERT_FILE` and `SSL_KEY_FILE` env vars, no plaintext fallback.
**Rationale:** Even Hub may reject plaintext HTTP. HTTPS is the proven-working baseline from the byoa-probe-spike.
**Alternatives considered:** Plaintext (risks confusing "SSE doesn't work" with "plaintext doesn't work").

### D8: Capture observations in `observations.md` like the byoa-probe-spike did

**Choice:** The user writes `openspec/changes/sse-tolerance-spike/observations.md` after running the probe, answering:
1. Did the HUD render any text at all in Scenario A?
2. Did the HUD render deltas progressively (token-by-token) or wait for [DONE]?
3. Which of the three flavors (`/openresponses`, `/openai-chunk`, `/raw`) worked, if any?
4. In Scenario B, did the HUD survive the 35s gap, time out, or show partial output?
5. Any errors visible on the HUD or in Even app logs?

**Rationale:** Same pattern that worked well for the byoa-probe-spike. Structured answers feed directly into the next change's design.
**Alternatives considered:** Capture observations verbally in chat (loses the artifact; harder to reference later).

## Risks / Trade-offs

- **[Risk] Glasses silently drop SSE connection on Content-Type mismatch** → **Mitigation:** Three flavor variants (D5) cover the likely formats the glasses' OpenAI-compatible client might expect. If none work, that's a definitive NO answer — itself a valuable result.
- **[Risk] Glasses consume SSE but enforce a total-response timeout (not idle)** → **Mitigation:** Scenario B (D4) explicitly tests this with a 35s gap. If Scenario A passes and B fails, we know SSE alone isn't enough; we'd need a richer architecture.
- **[Risk] Tailscale proxy buffers or strips SSE events** → **Mitigation:** Run probe on LAN first (faster to iterate); if it works on LAN but not over Tailscale, document as a separate finding about Tailscale + SSE interaction.
- **[Risk] uvicorn's default response handling breaks SSE** → **Mitigation:** Use FastAPI's `StreamingResponse` with `media_type="text/event-stream"` — known-working pattern. No middleware (gzip, etc.) that might buffer.
- **[Trade-off] Scripted response means we can't test real LiteLLM streaming** → Acceptable. We're testing the glasses, not LiteLLM. Real LiteLLM streaming is a follow-up if this spike passes.
- **[Trade-off] Three flavors × two scenarios = six probe runs** → ~5–10 minutes total on hardware. Cheap for the information gained.

## Migration Plan

This is a spike — there's no production migration. The spike's findings feed one of two future changes:

- **If SSE works (especially Scenario B)**: A future change adds SSE pass-through to `bridge-server/`. The bridge calls Hermes/LiteLLM with `stream: true`, forwards SSE events to the glasses (possibly translating OpenAI chat.completion.chunk ↔ OpenResponses events). No new glasses-app needed.
- **If SSE fails or doesn't survive long silence**: A future change builds (or adopts) a custom glasses-app + Hermes platform adapter, following the huntsyea architecture. Significantly more work but architecturally correct.

**Rollback for the spike:** `rm probe/sse_server.py` and remove the change directory. Nothing else to undo.

## Open Questions

These are the questions the spike is designed to answer — they remain open until the probe runs:

1. **Does the G2 BYOA client consume SSE at all?** — If NO on all three flavors, the answer is definitive: SSE is not a viable path.
2. **If yes, which SSE format does it expect?** — OpenResponses events, OpenAI chat.completion.chunk, or raw data lines?
3. **Does it render progressively or wait for `[DONE]`?** — Determines whether the user perceives streaming UX or just "faster total response."
4. **Does it survive a 35s silence between events?** — Determines whether SSE alone solves the slow-agent problem or just improves the common case.
5. **Does `: ping` keepalive help?** — We can add a follow-up test with ping comments if Scenario B fails on idle timeout.

No questions are deferred — the spike is small enough to answer all five in one run.
