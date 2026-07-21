## Context

`bridge-server/main.py` is a 379-line FastAPI + uvicorn app that today does:
1. WebSocket at `/ws/glasses` receives raw PCM16 16 kHz mono bytes from `glasses-app/` (a custom Even Hub plugin)
2. `webrtcvad` runs per-frame VAD on the audio stream to detect speech start/end
3. Complete utterances are wrapped as WAV and POSTed to LiteLLM's `/v1/audio/transcriptions` (Whisper STT)
4. Transcripts go to LiteLLM's `/v1/chat/completions` with `stream: true`; tokens stream back
5. Tokens are pushed to the glasses as ad-hoc JSON frames `{"type":"text","content":"..."}`

The archived probe spike (`openspec/changes/archive/2026-07-20-byoa-probe-spike/observations.md`) confirmed in detail what public reverse-engineering suggested: Even Hub v0.0.7+'s built-in "Add Agent" mode makes this entire audio side obsolete. The glasses do STT on-device and POST a plain OpenAI chat-completion request. Five questions were answered and three unexpected behaviors were discovered. The load-bearing findings that shape this design:

| Finding (from observations.md) | Source | Impact on this design |
|---|---|---|
| `body.user` is never populated | Q1 | Session key derived from client IP |
| `messages[]` is single-turn only (no history) | Q2 | Server MUST maintain history |
| Glasses fire duplicate parallel requests per utterance | 🔥 N1 | Server MUST dedupe within ~5 s window |
| Cold-start latency 17.3 s (warm: 1.7–2.3 s) | Q4, 🔥 N2 | Server SHOULD prewarm LiteLLM on boot |
| `accept-encoding: gzip` always sent | Q3 | Server SHOULD gzip responses |
| `User-Agent: Dart/3.11 (dart:io)` confirmed | Q5 | Used as glasses-traffic marker in logs |
| `model: "openclaw"` hardcoded | N4 | Server ignores field, rewrites to `CHAT_MODEL` |
| `Authorization: Bearer <user-token>` exact | N5 | Server enforces exact match against `BYOA_TOKEN` |
| Tailscale works as transport | N3 | No LAN-only assumption |
| SSE tolerance unknown | N6 | v1 ships non-streaming |

Constraints inherited from repo policy (`AGENTS.md`):
- Python via `uv` + `pyproject.toml` with `uv_build` backend, `src/<package>/` layout
- This is the "next touch" that triggers migrating `bridge-server/requirements.txt` to that layout

## Goals / Non-Goals

**Goals:**
- Replace the WS audio pipeline with a `POST /` BYOA endpoint that the glasses' built-in Add Agent mode talks to directly
- Honor all five confirmed protocol findings and three unexpected behaviors from the probe
- Enforce Bearer auth properly (the WS endpoint had none)
- Maintain conversation history server-side so the user experiences continuity across turns
- Deduplicate parallel duplicate requests so each utterance costs one LLM call, not two
- Prewarm LiteLLM on boot to absorb the 17 s cold-start cost
- Migrate `bridge-server/` to the repo's `uv` + `pyproject.toml` + `src/` standard
- Preserve `glasses-app/` as-is (legacy fallback; a future change can remove it)

**Non-Goals:**
- SSE streaming — deferred to a possible future change (probe couldn't confirm glasses accept SSE; non-streaming is the safe v1)
- Multi-user support (history keyed by client IP; multi-tenant deployment is out of scope)
- HUD error-message tuning (probe didn't pin down what the HUD shows for 401/500/timeout; v1 returns standard HTTP statuses and OpenAI-shape errors)
- Response character-limit truncation (probe didn't measure the HUD truncation threshold; v1 returns full responses and lets the glasses truncate)
- Deleting `glasses-app/` (separate change once BYOA path is proven in production)
- Tool/function-calling or "agent" protocol layers (glasses don't send any of this; nothing to plumb)
- WebSocket backwards-compatibility (BREAKING — the old `/ws/glasses` endpoint goes away cleanly)

## Decisions

### D1: New package `byoa_bridge` under `bridge-server/src/`, full uv migration

**Choice:** Convert `bridge-server/` from a flat `main.py` + `requirements.txt` to the repo-standard `pyproject.toml` + `uv_build` + `src/byoa_bridge/` layout (per `AGENTS.md`). The server module is `byoa_bridge.server:app`.
**Rationale:** This change touches >70% of `main.py` anyway — it's the natural moment to migrate. The new module name (`byoa_bridge`) reflects the new role (BYOA bridge, not WS audio bridge) without colliding with the disposable `byoa_probe` probe package.
**Alternatives considered:** Keep flat `main.py` + `requirements.txt` and add `pyproject.toml` only (mixes old and new conventions; violates `AGENTS.md`). Rename in place to `bridge_server` (underscores work for Python but the repo convention is hyphenated package dirs + underscored modules).

### D2: Single `POST /` route, no WS, no extra routes

**Choice:** Exactly one application route: `POST /`. Plus `GET /health` for deployment health checks. No `/ws/glasses`, no `/v1/chat/completions` alias.
**Rationale:** The glasses POST to root and nothing else (probe confirmed). Adding an OpenAI-shape alias (`/v1/chat/completions`) is a non-goal — would invite confusion about whether this server is a general OpenAI proxy.
**Alternatives considered:** Keep `/ws/glasses` alive but return 410 Gone (overkill — we control the only client and it's being retired).

### D3: Bearer auth via constant-time comparison

**Choice:** Read `Authorization` header; require exact string match `Bearer {BYOA_TOKEN}` against the env-var-configured token, using `hmac.compare_digest` to avoid timing side channels. Reject mismatches with HTTP 401 + `{"error":{"message":"unauthorized","type":"auth_error"}}` (OpenAI-style error shape, since the glasses are an OpenAI-compatible client and may render the error message).
**Rationale:** Probe confirmed the glasses send exactly `Bearer <token>` with no transformation. Constant-time comparison matters because the token is a bearer secret — timing leaks would let an attacker recover it byte-by-byte.
**Alternatives considered:** Plain `==` comparison (vulnerable to timing attack, even on LAN). Per-token ACLs (overkill for single-user deployment).

### D4: Server-side history keyed by client IP, capped at `MAX_HISTORY_TURNS`

**Choice:** Maintain a module-level `dict[client_ip, list[message]]`. On each accepted request:
1. Look up history by `request.client.host`
2. Build forwarded messages = `[system] + history + [new_user_msg]`
3. Forward to LiteLLM
4. On success: append `user_msg` and `assistant_reply` to history; truncate to last `MAX_HISTORY_TURNS * 2` entries (each turn is 2 messages)
5. On LiteLLM error: do NOT append anything (failed turn doesn't pollute history)
**Rationale:** Probe confirmed glasses send only the current user message — server MUST own history. Client IP is the only stable per-glasses identifier in the request (`body.user` is absent). Cap prevents unbounded memory growth.
**Trade-offs:**
- All glasses behind the same NAT share a history — acceptable for single-user LAN/Tailscale deployment; flagged as a limitation if we ever care about multi-user
- In-memory history is lost on restart — acceptable; conversations are short and the user just re-establishes context
**Alternatives considered:** Persistent history (SQLite/Redis — overkill, adds deps and operational surface). History keyed by `x-openclaw-agent-id` (always `main`, so same as no keying at all).

### D5: Request deduplication via in-flight + recent-result cache

**Choice:** Two-layer dedup keyed on `(client_ip, sha256(latest_user_content))`:
1. **In-flight cache**: If a request with the same dedup key is currently being processed (LiteLLM call not yet returned), the second request awaits the same `asyncio.Task` and shares the result. This is the common case per the probe (TURN 2+3 fire in parallel within the same second).
2. **Recent-result cache**: For `DEDUP_WINDOW_SECONDS` (default 5) after a request completes, an identical request returns the cached response instead of re-calling LiteLLM. Covers the case where the glasses re-fire after a brief delay (e.g., TURN 1 cold-start → user re-prompts ~40s later — this would NOT be deduped because content differs, but if they re-prompt with the same words within 5s it would be).
**Rationale:** Probe measured 2× LLM cost per utterance without dedup. With Claude Sonnet via LiteLLM, that's real money. The in-flight cache also cuts wall-clock latency on the duplicate path (it shares the first request's result instead of running a parallel LiteLLM call that competes for the same model).
**Concurrency safety:** The in-flight cache uses an `asyncio.Lock` per dedup key to make the check-then-set atomic within the event loop. The recent-result cache is a simple dict with timestamps; cleanup happens lazily on insert.
**Trade-off:** If the user legitimately says the same thing twice in a row within `DEDUP_WINDOW_SECONDS`, the second utterance gets the cached reply. Acceptable — rare in practice, and the user can always vary their wording or wait 5 s.
**Alternatives considered:** No dedup (2× cost, kills the budget). Dedup by message content only (would over-dedupe across different glasses/IPs). Dedup by request fingerprint including headers (over-engineered — headers are stable).

### D6: LiteLLM prewarm on FastAPI startup

**Choice:** On the FastAPI `lifespan` startup event, fire-and-forget a tiny `POST /v1/chat/completions` to LiteLLM with `messages: [{"role":"user","content":"ping"}]`, `max_tokens: 1`. Don't `await` it — let it run in the background while uvicorn finishes coming up. Log but don't fail on error.
**Rationale:** Probe measured 17.3 s cold-start on the first real request. Prewarm shifts that cost off the user's first utterance and onto server boot, which is unobservable to the user. The glasses' ~30 s timeout means a 17 s cold-start on the first real request risks timing out — prewarm eliminates that risk.
**Trade-off:** One wasted LLM call per server boot. Cheap for local Qwen models, ~$0.0001 for Claude Sonnet. Negligible.
**Alternatives considered:** Block startup until prewarm completes (delays readiness — bad for rolling deploys). Skip prewarm (risks first-utterance timeout). Periodic keep-alive pings (adds complexity; warm model stays warm for hours).

### D7: Non-streaming response to glasses; non-streaming call to LiteLLM

**Choice:** `stream: false` to LiteLLM, await full reply, return one OpenAI chat-completion JSON blob to the glasses.
**Rationale:** Probe confirmed the dAAAb canonical reference uses non-streaming at scale. SSE tolerance is unknown (deferred per N6). Non-streaming is the safe, known-good v1.
**Trade-off:** User perceives full response latency before any HUD update (no token streaming). With prewarm + warm LiteLLM (probe measured 1.7–2.3 s), this is acceptable.
**Alternatives considered:** Streaming from LiteLLM, buffer to non-streaming reply to glasses (best of both worlds — but adds complexity without observable user benefit since the glasses don't stream). SSE all the way through (blocked on unknown glasses support — separate future change).

### D8: gzip-enabled responses

**Choice:** Add `GZipMiddleware` from `fastapi.middleware.gzip` with `minimum_size=512`. Responses over 512 bytes are gzip-compressed when the client sends `accept-encoding: gzip`.
**Rationale:** Probe confirmed glasses send `accept-encoding: gzip`. For typical LLM replies (often 500–2000 chars), gzip cuts wire bytes ~5–10×. The CPU cost is negligible. FastAPI's middleware handles the `accept-encoding` negotiation automatically — clients that don't ask for gzip get plaintext.
**Alternatives considered:** Manual gzip per-response (more control, more code). No gzip (wastes bandwidth; the glasses are on a constrained wireless link).

### D9: `truststore` for upstream TLS, drop `SSL_CA_FILE`

**Choice:** Add `truststore>=0.10.0` dependency. Call `truststore.inject_into_ssl()` as the first statement in `byoa_bridge/server.py`, before any httpx/uvicorn import. Drop `SSL_CA_FILE` env var and the `_build_client_ssl_ctx()` function from the old `main.py`.
**Rationale:** The user's local CA is installed in the OS trust store (per probe N3, that's how Tailscale's HTTPS cert validation worked). `truststore` makes Python's `ssl` module use the OS trust store automatically — httpx validates LiteLLM's cert without any explicit CA configuration.
**Trade-off:** Adds one dep. Truststore is small (~10 KB), pure-Python, well-maintained by the urllib3/requests maintainer Seth Larson. Used in production by major Python projects.
**Alternatives considered:** Keep `SSL_CA_FILE` pattern (works but requires the user to point at the CA file explicitly — duplicative when the CA is already in the OS trust store). Don't validate upstream cert (insecure; would mask MITM).

### D10: Drop `python-json-logger`, use stdlib logging with KV extras

**Choice:** Use `logging.Logger.info(msg, extra={...})` with stdlib's default formatter. Drop `python-json-logger` dependency. Log format: `"%(asctime)s %(levelname)s %(name)s %(message)s"` plus any structured extras serialized into the message line as `key=value`.
**Rationale:** The old `main.py` used JSON logs because the audio pipeline had ~15 different event types with rich metadata. The new server has ~5 event types (request received, dedup hit, LiteLLM forwarded, LiteLLM responded, error). Plain-text logs are easier to grep on a single-user server and `docker logs` shows them readably.
**Trade-off:** Loses structured parsing if the user ever wants to ship logs to a SIEM. Acceptable for a personal bridge server. A future change could re-add JSON formatting if log volume grows.
**Alternatives considered:** Keep `python-json-logger` (carries the dep for marginal benefit at the new smaller log volume). `structlog` (heavier dep, same outcome).

### D11: Keep `glasses-app/` untouched in this change

**Choice:** `glasses-app/` directory and its `app.json` are not modified or deleted by this change.
**Rationale:** Deleting `glasses-app/` is a separate decision that should happen only after BYOA is confirmed stable in production for some period. Keeping it preserves a fallback path if the BYOA mode has unforeseen problems. The `app.json` network whitelist still references `hermes.local` — unused after this change but harmless.
**Alternatives considered:** Delete `glasses-app/` in this change (cleanup is satisfying but removes a fallback path during the riskiest first deployment). Update `glasses-app/` to also use BYOA (defeats the purpose of BYOA — the plugin becomes redundant).

## Risks / Trade-offs

- **[Risk] Glasses timeout if LiteLLM call exceeds ~30 s** → **Mitigation:** Prewarm on boot absorbs the 17 s cold-start cost. Warm calls measured at 1.7–2.3 s. Server logs per-request latency and surfaces a warning at >10 s. If the user's model is consistently slow, they can swap to a smaller model in LiteLLM config — out of this server's scope.
- **[Risk] First-utterance failure if prewarm hasn't completed** → **Mitigation:** Prewarm is fire-and-forget; if the glasses POST arrives before prewarm completes, the user pays the cold-start cost (same as today). Prewarm only ever helps; it never hurts.
- **[Risk] Dedup false-positive on legitimate repeat utterance within window** → **Mitigation:** Window is short (5 s default). The known case is the user saying "what time is it? ... what time is it?" within 5 s — rare, and the cached reply is still correct. Tunable via `DEDUP_WINDOW_SECONDS` env var.
- **[Risk] Client IP collisions in multi-user NAT** → **Mitigation:** Out of scope for v1 (single-user deployment). Documented as a known limitation. If it ever matters, a future change could add per-glass authentication tokens.
- **[Risk] `glasses-app/` plugin breaks silently if someone tries to use it against the new server** → **Mitigation:** The WS endpoint is gone; the plugin's WS connection will fail to connect with a clear `404 Not Found`. The plugin logs the error. No silent failure mode.
- **[Risk] Token replay if `BYOA_TOKEN` leaks** → **Mitigation:** Constant-time comparison prevents timing-based extraction. Token is bound to the glasses' Add Agent entry — if leaked, the user changes the token in both places. Out of scope: mTLS, per-glass tokens (future work if threat model ever justifies).
- **[Trade-off] In-memory history lost on restart** → Acceptable for a personal assistant. Conversations don't span hours; if the server restarts mid-conversation, the user re-establishes context. Persistent storage would add operational complexity disproportionate to the value.
- **[Trade-off] No SSE streaming** → First response latency = full LLM latency. With prewarm + warm 1.7–2.3 s calls, acceptable. If first-token latency becomes a real UX problem, a future change can probe SSE tolerance and add streaming.

## Migration Plan

This is a one-shot migration, not a gradual rollout. Single user, single deployment.

1. **Build new code in `bridge-server/src/byoa_bridge/`** (new module; old `main.py` untouched during build)
2. **Write `bridge-server/pyproject.toml`** with `uv_build` + deps (fastapi, uvicorn, httpx, truststore)
3. **Run `uv sync`** to verify env works
4. **Smoke test locally** with curl against the new POST / endpoint (no glasses required)
5. **Update `bridge-server/Dockerfile`** to use `uv run uvicorn byoa_bridge.server:app` as the entrypoint
6. **Update `docker-compose.yml`** — remove WS-specific Traefik labels, keep port 8765
7. **Update `.env.example`** — drop audio vars, drop `SSL_CA_FILE`, add `BYOA_TOKEN`, add `DEDUP_WINDOW_SECONDS`
8. **Delete old `bridge-server/main.py`** and `bridge-server/test-client.html` and `bridge-server/requirements.txt`
9. **User deploys:** `docker compose up -d --build`
10. **User reconfigures Even app:** Settings → Add Agent → URL `https://<server>:8765`, Token = `BYOA_TOKEN` value
11. **Validate end-to-end:** user says "my name is X" → server logs POST with that content → LiteLLM responds → glasses display reply. Then user says "what's my name?" → server reuses history → LiteLLM reply includes the name.

**Rollback:** `git revert` the merge commit + `docker compose up -d --build`. The old image is the rollback target. User reconfigures Even app to use `glasses-app/` again if needed (the plugin was not deleted).

## Open Questions

None blocking. The probe spike resolved all blocking unknowns. The deferred items (SSE tolerance, HUD error rendering, char-limit truncation) are explicitly out of scope for v1 and can be probed in a future spike if they become relevant.
