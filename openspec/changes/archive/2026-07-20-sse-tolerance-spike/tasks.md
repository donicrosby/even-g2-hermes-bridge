## 1. Probe server implementation

- [x] 1.1 Create `probe/sse_server.py` with a FastAPI app, four POST routes (`/`, `/openresponses`, `/openai-chunk`, `/raw`), and a `GET /health` route
- [x] 1.2 Implement the canned SSE event generator that emits the scripted "Hello, world. Testing SSE." content with configurable delays read from env vars (`SSE_DELAY_CREATED`, `SSE_DELAY_IN_PROGRESS`, `SSE_DELAY_FIRST_DELTA`, `SSE_DELAY_BETWEEN_DELTAS`, `SSE_DELAY_BEFORE_COMPLETED`)
- [x] 1.3 Implement the OpenResponses flavor (`/` and `/openresponses`): `event: response.created`, `event: response.in_progress`, `event: response.output_text.delta`, `event: response.completed`, `data: [DONE]`
- [x] 1.4 Implement the OpenAI chat.completion.chunk flavor (`/openai-chunk`): `data: {"choices":[{"delta":{"content":"..."}}]}`, `data: [DONE]`
- [x] 1.5 Implement the raw flavor (`/raw`): plain `data: <text>` lines, no event prefix
- [x] 1.6 Use `fastapi.responses.StreamingResponse` with `media_type="text/event-stream"` and disable any middleware (gzip, etc.) that could buffer the stream
- [x] 1.7 Add `Cache-Control: no-cache` and `Connection: keep-alive` headers to SSE responses
- [x] 1.8 Log every POST request with client IP, headers verbatim (including Authorization value), and parsed body summary — same format as the prior byoa-probe-spike

## 2. TLS and env-var enforcement

- [x] 2.1 Require `SSL_CERT_FILE` and `SSL_KEY_FILE` to be set and exist on disk; refuse to start with a clear error otherwise
- [x] 2.2 Verify no plaintext fallback path exists (HTTPS-only)
- [x] 2.3 Add `HOST`, `PORT`, `LOG_LEVEL` env vars with sensible defaults (HOST=0.0.0.0, PORT=8766 to avoid clashing with the prior byoa_probe on 8765, LOG_LEVEL=INFO)

## 3. README additions

- [x] 3.1 Add an "SSE tolerance probe" section to `probe/README.md` documenting: prerequisite (existing local CA), setup (env vars), launch commands for both scenarios, what utterances to say, what to observe on the HUD, and how each outcome maps to an architecture decision
- [x] 3.2 Include literal `uv run --env-file .env uvicorn sse_server:app ...` commands, not pip or venv activation
- [x] 3.3 Document the three flavors and when to try each (start with `/openresponses`, fall back to `/openai-chunk` then `/raw` if it doesn't work)
- [x] 3.4 Document the two scenarios (normal ~7s total, stress 35s first-delta delay) with their exact env-var presets

## 4. Smoke test (no glasses required)

- [x] 4.1 Start the probe locally with valid TLS env vars and verify `curl -k https://localhost:8766/health` returns `{"status":"ok","mode":"sse-probe"}`
- [x] 4.2 POST to `/openresponses` with curl and verify the SSE event sequence is correct (response.created → response.in_progress → 3s pause → response.output_text.delta × 3 → response.completed → [DONE])
- [x] 4.3 POST to `/openai-chunk` with curl and verify the OpenAI chat.completion.chunk SSE shape
- [x] 4.4 POST to `/raw` with curl and verify raw data lines
- [x] 4.5 Override `SSE_DELAY_FIRST_DELTA=5` and verify the delay is respected
- [x] 4.6 Verify the probe refuses to start when `SSL_CERT_FILE` or `SSL_KEY_FILE` is unset
- [x] 4.7 Confirm `git status` shows only `probe/sse_server.py` and the change directory — no production code touched

## 5. Real-hardware probe (USER RUNS THIS)

- [x] 5.1 Configure Even app Add Agent entry to point at `https://<probe-LAN-IP>:8766` with any token
- [x] 5.2 **Scenario A — normal timing, `/openresponses` flavor**: long-press touchbar, say "hello", wait for HUD reaction, capture what the HUD showed (full text, partial text, error, nothing) — **RESULT: network error**
- [x] 5.3 **Scenario A — `/openai-chunk` flavor**: same utterance, observe HUD — **RESULT: network error**
- [~] 5.4 **Scenario A — `/raw` flavor**: NOT RUN — both structured flavors failed identically, raw text would fail at the same Content-Type rejection layer; deemed not worth the additional probe cycle
- [~] 5.5 **Scenario B — stress timing**: NOT RUN — Scenario A already failed definitively; testing whether SSE survives 35s silence is moot when SSE doesn't work at all
- [x] 5.6 Note any HUD-rendered errors, partial renders, or "thinking" indicators during the pauses — **RESULT: clean "network error" message, no partial renders, no thinking indicators**
- [~] 5.7 Check Even app logs (if accessible) — NOT RUN — Even app log access not available during probe; server-side 200 OKs are sufficient evidence

## 6. Capture observations and architecture decision

- [x] 6.1 Write `openspec/changes/sse-tolerance-spike/observations.md` answering the five open questions from `design.md`: (1) did the HUD render any text at all, (2) progressively or wait-for-[DONE], (3) which flavor worked, (4) did Scenario B's 35s gap survive, (5) any errors visible
- [x] 6.2 In `observations.md`, state the architecture decision the findings point to: **SSE non-viable** — all tested flavors failed; next change must build/adopt a custom glasses-app + Hermes platform adapter
- [x] 6.3 Note any unexpected glasses-side behaviors — duplicate-fire pattern re-confirmed for `/openai-chunk` flavor (Turns 4+5 at 01:02:57); rejection happens at Content-Type layer, before body parsing
- [x] 6.4 Verify `git status` is clean outside `probe/` and `openspec/changes/sse-tolerance-spike/`
- [x] 6.5 Do NOT archive yet — the observations feed the next change (build/adopt custom glasses-app + Hermes platform adapter)
