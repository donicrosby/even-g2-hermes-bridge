## Context

`bridge-server/` today speaks a custom protocol: a WebSocket at `/ws/glasses` that receives raw PCM16 16 kHz mono audio from a custom Even Hub SDK plugin (`glasses-app/`), runs server-side WebRTC VAD + Whisper STT, streams LLM tokens back as `{type:"text",content}` JSON frames. This works but requires the user to install and maintain a custom glasses-side app.

Even Hub v0.0.7+ ships a built-in "Add Agent" mode that lets the user configure a custom AI endpoint directly in the companion app (Settings → Add Agent → Name, URL, Token). When configured, the G2 glasses' own UI does on-device STT and POSTs a chat-completion request to the user's URL. This obsoletes the custom `glasses-app/` plugin and most of the server-side audio pipeline.

Public reverse-engineering (blog.juchunko.com packet capture, the `dAAAb/openclaw-even-g2-bridge-skill` Cloudflare Worker, the `CI-Even-Realities` project) gives us high confidence on the wire protocol:

- `POST /` with `Authorization: Bearer <token>`, `Content-Type: application/json`, `User-Agent: Dart/3.8 (dart:io)`, `x-openclaw-agent-id: main`
- Body: `{"model":"openclaw","messages":[{"role":"user","content":"..."}]}`
- Response: plain-JSON OpenAI chat-completion object (non-streaming confirmed working at scale)
- ~30 s glasses-side timeout; dAAAb caps server work at 22 s

But five behavioral unknowns remain unverified against real hardware, and each one shapes the production migration:

1. **Multi-turn history ownership** — does the glasses' `messages[]` array grow turn-by-turn, or does it send only the latest user message and expect server-side history? The dAAAb worker maintains history server-side keyed by `user: "g2-glasses"`, which suggests glasses do NOT echo history. Confirm.
2. **`body.user` presence** — does the glasses populate the OpenAI `user` field, or does the server have to derive a session key some other way?
3. **HUD error rendering** — what does the glasses display render for 401, 500, and timeout? Determines how we communicate failures to the user.
4. **SSE tolerance** — does the BYOA client consume `text/event-stream` if we return it, or only plain JSON? Determines whether we can ship streaming in the production migration.
5. **Response character limits** — what's the practical maximum before the HUD truncates or errors? Determines truncation policy.

Constraints:
- User has a working local CA for HTTPS (Even Hub may reject plain HTTP for non-LAN URLs; HTTPS is the safe baseline)
- User has a working LiteLLM upstream with API key in env
- G2 hardware in hand
- Probe must be disposable — must not pollute `bridge-server/` or `glasses-app/`

## Goals / Non-Goals

**Goals:**
- Stand up a probe server in under an hour that the user can point the G2 Add Agent at and converse end-to-end with their real LiteLLM
- Capture the raw glasses request (headers verbatim, full body, client IP, timestamp, turn number) to a structured log on every POST
- Resolve all five behavioral unknowns by reading the log and observing HUD behavior
- Stay fully isolated from production code so cleanup is `rm -rf probe/`

**Non-Goals:**
- Implement the production BYOA migration — that's a separate change built on the probe's findings
- Maintain server-side conversation history during the probe — pass-through only, so we can observe what the glasses actually send
- Enforce Bearer auth — accept any token, log it, so we observe what the glasses send
- Handle SSE or error responses in v1 of the probe — those are separate probe modes (see Decisions)
- Containerize, deploy, or integrate with docker-compose / Traefik — runs as a bare `uvicorn` process

## Decisions

### D1: Probe lives in `probe/` at repo root, not inside `bridge-server/`

**Choice:** New top-level `probe/` directory.
**Rationale:** Total isolation from production code. Cleanup is `rm -rf probe/`. No risk of accidentally shipping probe code in the production container.
**Alternatives considered:** Sibling file inside `bridge-server/` (mixes debug + production), separate git repo (overhead for a disposable spike).

### D2: FastAPI + uvicorn + uv, same stack as `bridge-server/`

**Choice:** Reuse the FastAPI + uvicorn + httpx stack from `bridge-server/`, declared in `probe/pyproject.toml` and managed with `uv` per the repo-root `AGENTS.md`. Add `truststore` so the upstream httpx call to LiteLLM uses the system trust store (where the user's local CA is installed) instead of certifi's bundled store.
**Rationale:** Minimal new dependencies. User already has `uv` available (the repo-wide Python tooling standard). Identical SSL env-var pattern (`SSL_CERT_FILE` / `SSL_KEY_FILE`) for the glasses-facing side so the local CA cert "just works." For the upstream side, `truststore.inject_into_ssl()` is called as the very first statement in `server.py` (before any httpx/uvicorn import) so httpx's default SSLContext picks up the system trust store automatically — no `SSL_CA_FILE` env var needed.
**Alternatives considered:** stdlib `http.server` (too spartan for JSON body handling), aiohttp (different dependency set), Flask (sync — would block on LiteLLM calls), `pip` + `requirements.txt` (explicitly forbidden by `AGENTS.md`), `SSL_CA_FILE` env var pattern from `bridge-server/` (works but requires the user to point at the CA file explicitly; truststore uses the OS trust store where they've already installed their local CA — less duplication).

### D3: HTTPS-only via user's local CA, no plaintext mode

**Choice:** Require `SSL_CERT_FILE` and `SSL_KEY_FILE` to be set. Refuse to start without them.
**Rationale:** User confirmed a working local CA is available. Even Hub may reject plain HTTP for non-LAN URLs. HTTPS removes a class of "does it even connect" unknowns and matches the production target.
**Alternatives considered:** Plaintext-with-Traefik pattern from `bridge-server/` (overkill for a probe), auto-self-signed cert generation (mistrust complications).

### D4: Single endpoint `POST /` — production target shape only

**Choice:** Implement the one route the glasses will hit. No additional probe-mode endpoints in v1.
**Rationale:** The probe's primary job is to resolve multi-turn and `body.user` unknowns — those need a working end-to-end flow, not specialized routes. SSE and error-HUD probing can be done as a follow-up by editing one flag in the response builder, not by adding more routes.
**Alternatives considered:** `/echo`, `/sse`, `/slow/{seconds}`, `/status/{code}` endpoints (deferred — they're a separate, smaller spike once the baseline flow is confirmed).

### D5: Pass-through forwarding — no server-side history

**Choice:** Whatever `messages[]` the glasses send gets forwarded to LiteLLM unchanged (plus a `system` message prepended and `model: "openclaw"` rewritten to `CHAT_MODEL`).
**Rationale:** The probe's whole purpose is to OBSERVE whether the glasses maintain history. If we maintain it server-side, we mask the glasses' behavior and learn nothing.
**Trade-off:** If the glasses send only the latest user message, the conversation will be single-turn from the LLM's perspective — the user will experience "no memory" during the probe. This is acceptable and instructive; it confirms the unknown.
**Alternatives considered:** Server-side history with a toggle (adds complexity and a state-management surface we don't need for a 30-minute probe).

### D6: Bearer auth accepted-but-logged, not enforced

**Choice:** Read `Authorization` header, log it verbatim, never reject with 401.
**Rationale:** We want to observe the exact token format the glasses send (is it `Bearer <user-token>`? `Bearer <some-internal-id>`? something else?). Enforcing would block the probe on a wrong guess.
**Trade-off:** The probe endpoint is unauthenticated on the LAN. Acceptable for a temporary spike on a private network. Production migration MUST enforce Bearer.

### D7: Structured plain-text log to `probe.log` + stdout

**Choice:** Append every request as a clearly delimited plain-text block to `probe.log` in the working directory, and also print to stdout for live observation.
**Rationale:** Plain text is greppable, diffable, and pastable into the next planning session. No JSON-log parser needed. Delimiter blocks make turn 1 / turn 2 visually obvious.
**Format:**
```
=== TURN N — <ISO timestamp> ===
CLIENT: <ip>:<port>
METHOD: POST
PATH: <path>
HEADERS (verbatim):
  <name>: <value>     ← preserves original casing
  ...
BODY (raw):
  <raw JSON string>
BODY (parsed):
  model: <value>
  user: <present? absent? value?>
  messages:
    - role: <role>, content: <first 200 chars>
    - ...
  <other fields>
LITELLM_REQUEST:
  model: <rewritten value>
  messages_count: <N>
LITELLM_RESPONSE:
  status: <HTTP code>
  content_chars: <N>
  latency_ms: <ms>
=== END TURN N ===
```
**Alternatives considered:** JSON logs (harder to eyeball), SQLite store (overkill for ~5 turns of probing), no persistent log (loses the data we came to capture).

### D8: Response builder follows the dAAAb canonical shape exactly

**Choice:** Return:
```json
{
  "id": "g2-probe-<uuid8>",
  "object": "chat.completion",
  "created": <unix_ts>,
  "model": "g2-probe",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "<LLM reply>"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": <len>, "total_tokens": <len>}
}
```
**Rationale:** This is the exact shape the dAAAb worker returns and is confirmed working at scale. No reason to deviate for the probe.
**Alternatives considered:** Minimal `{choices:[{message:{content}}]}` only (works but loses fidelity if glasses validate other fields), strict OpenAI echo (no observed benefit).

### D9: LiteLLM call uses non-streaming

**Choice:** `stream: false` to LiteLLM, await full response, return as one blob.
**Rationale:** Matches the dAAAb canonical reference. Streaming probe is a separate decision (see Open Questions).
**Trade-off:** User waits for full LLM response before HUD update. Acceptable for a probe; the production migration may revisit.

### D10: No conversation history across turns in the probe

**Choice:** Each request is fully independent from the LLM's perspective.
**Rationale:** Goal of probe is to see if the GLASSES maintain history. Server-side history would mask this.
**Trade-off:** User will observe "amnesia" across turns during the probe. This is the intended signal: if the user says "my name is Don" then "what's my name?" and the LLM doesn't know, we've confirmed the glasses don't echo history and the production migration MUST maintain it server-side.

## Risks / Trade-offs

- **[Risk] LiteLLM `model: "openclaw"` rejection** → **Mitigation:** Rewrite `model` to `CHAT_MODEL` before forwarding. If LiteLLM still rejects (e.g. unknown model name in user's config), the probe logs the error and returns a friendly chat-completion error message to the HUD.
- **[Risk] Glasses-side timeout (~30s) exceeded by cold LiteLLM call** → **Mitigation:** Probe logs latency on every turn. If we observe >22s responses, we surface it as a finding (the production migration needs the same 22s budget). No mitigation in the probe itself — we want to observe real latency.
- **[Risk] Glasses reject the response shape** → **Mitigation:** Probe returns the exact dAAAb canonical shape. If the glasses still reject, that's itself a finding — we'll see it as "HUD shows error" and can iterate on the response builder.
- **[Risk] HTTPS cert trust failure on glasses** → **Mitigation:** User has a working local CA already trusted on the phone. If the glasses still reject, fall back to adding the cert to the phone's trust store explicitly.
- **[Risk] User can't tell which probe findings are actionable** → **Mitigation:** `probe/README.md` enumerates exactly what to look for in the log and what each observation implies for the production migration.
- **[Trade-off] No SSE / error-HUD probing in v1** → If the production migration needs SSE streaming or wants graceful HUD error rendering, we'll need a follow-up spike. This is acceptable — those are refinements, not blockers, and the primary probe (multi-turn + body.user) is the load-bearing one.
- **[Trade-off] Probe accepts any Bearer token** → Insecure on untrusted LAN. Acceptable for the spike; documented in README.

## Migration Plan

This change is a spike — there is no production migration for the spike itself. The probe's findings feed into a separate, future change (`byoa-protocol-migration` or similar) that will:
1. Migrate `bridge-server/main.py` to the BYOA HTTP shape
2. Delete the WebSocket endpoint, VAD, Whisper call, and `webrtcvad` dependency
3. Delete or archive `glasses-app/`
4. Update `docker-compose.yml` / Traefik routing for HTTP instead of WS
5. Enforce Bearer auth properly
6. Implement server-side history based on the probe's findings about glasses-side behavior

**Rollback for the spike:** `rm -rf probe/` and remove the change directory. Nothing else to undo.

## Open Questions

These are the questions the probe is designed to answer — they remain open until the probe runs and we read the log:

1. **Does `body.user` get populated by the glasses?** If yes → production can use it as the session key. If no → production derives session key from client IP, `x-openclaw-agent-id`, or a synthetic per-glasses id.
2. **Does `messages[]` grow turn-by-turn?** If yes → production can pass through to LiteLLM directly (Option A). If no → production MUST maintain history server-side (Option B).
3. **Are there any unexpected headers or body fields?** The probe logs everything verbatim, so anything we didn't anticipate shows up.
4. **What's the real end-to-end latency distribution?** Probe logs `latency_ms` per turn; tells us whether the 22s budget is realistic for the user's LiteLLM setup.
5. **Does the glasses' `User-Agent` stay `Dart/3.8 (dart:io)` across Even Hub versions, or has it changed in v0.0.12?** Probe captures it verbatim.

Questions deferred to a follow-up spike (not this one):
- Does the BYOA client consume SSE if we return `text/event-stream`?
- What does the HUD render for 401, 500, and timeout?
- What's the practical character limit before HUD truncates?

The deferred questions are answered by adding `/sse`, `/status/{code}`, `/slow/{seconds}`, and `/length/{chars}` routes in a v2 probe — small additive changes after v1 of the probe is working.
