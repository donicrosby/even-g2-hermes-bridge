## Why

The `bridge-server-byoa-migration` shipped a working synchronous BYOA endpoint (POST `/` returning one JSON chat-completion blob). That works for short prompts but breaks down when the agent takes more than ~30s to respond (long reasoning, code generation, multi-tool chains): the glasses' BYOA client times out, the user sees an error, and the late response is lost. Public OpenClaw spec research (`openspec/changes/archive/2026-07-20-byoa-probe-spike/observations.md` plus librarian findings on the OpenClaw `/v1/responses` endpoint) confirms OpenClaw supports SSE streaming with `event: response.in_progress`, `event: response.output_text.delta`, and `: ping` keepalives every 30s — all of which would let the glasses survive long agent responses **if and only if** their built-in Add Agent client consumes `text/event-stream`. That single behavioral unknown is load-bearing for the next architecture decision (SSE pass-through vs. custom glasses-app vs. lazy delivery). This spike answers it with a 30-minute probe against real G2 hardware.

## What Changes

- **Add** `probe/sse_server.py` — a small FastAPI app that always returns `Content-Type: text/event-stream` on POST `/`, emitting a scripted sequence of OpenResponses-style SSE events with configurable artificial delays between them
- **Add** `probe/sse_run.sh` (or document inline in README) — convenience launcher that sets the required env vars and starts the SSE server on a configurable HTTPS port using the user's existing local CA
- **Update** `probe/README.md` — add an "SSE tolerance probe" section with the utterances to say, what to observe on the HUD, and how each outcome maps to an architecture decision
- **Add** `openspec/changes/sse-tolerance-spike/observations.md` (written by the user after the probe runs, like the BYOA spike pattern) — captures what the HUD did for each scenario
- **No changes** to `bridge-server/`, `glasses-app/`, or any production code — this spike is fully contained in the existing `probe/` directory and disposable

## Capabilities

### New Capabilities
- `sse-tolerance-probe`: Temporary SSE-emitting HTTP server that mimics the OpenResponses streaming protocol surface so we can observe whether the G2 glasses' built-in Add Agent client consumes `text/event-stream` or rejects it. Resolves the load-bearing unknown for the next architecture decision.

### Modified Capabilities
<!-- None — this spike touches no production code. -->

## Impact

- **Code**: Adds one Python module (`probe/sse_server.py`, ~120 lines) and a launcher script. Reuses the existing `probe/pyproject.toml` and `.venv` from the prior BYOA probe — no new deps, no new package.
- **Dependencies**: None new. FastAPI + uvicorn + truststore already in `probe/pyproject.toml`.
- **Runtime**: Temporary local process run during the hardware probe. Same launch shape as the prior BYOA probe (`uv run --env-file .env uvicorn ...`).
- **Cleanup**: `probe/sse_server.py` is disposable after the architecture decision. Leave the `probe/` directory intact for now (pending the broader cleanup that also removes the original `byoa_probe` server).
- **Security**: HTTPS via the user's local CA (same as prior probes). Bearer auth accepted-but-logged, not enforced — this is an observation probe.
- **Outcome**: The spike is successful if it produces a clear YES/NO/PARTIAL answer to "do the glasses consume SSE?" captured in `observations.md`. The answer determines whether the next change is "SSE pass-through in byoa-bridge" (cheap) vs. "build/adopt a custom glasses-app + Hermes platform adapter" (expensive).
