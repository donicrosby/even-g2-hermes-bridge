# BYOA Probe Observations

**Captured:** 2026-07-20 ~19:17–19:19 UTC
**Source:** `probe/probe.log` (5 turns of real G2 hardware traffic)
**Network path:** G2 → phone (Even Hub v0.0.12) → Tailscale (`your-host.your-tailnet.ts.net:8765`) → probe server → LiteLLM (`litellm.local`, model `your-model-name`)

## Raw data summary

| Turn | Timestamp (start) | Content (first 60 chars) | content_length | Latency (ms) | Response chars |
|---|---|---|---|---|---|
| 1 | 19:17:59 | "What time is it?" | 78 | **17356** | 31 |
| 2 | 19:18:57 | "What time is it?" (same) | 78 | 2320 | 77 |
| 3 | 19:18:57 | "What time is it?" (same) | 78 | 1815 | 61 |
| 4 | 19:19:12 | "Um, can you tell me what time the daylight savings..." | 121 | 1704 | 188 |
| 5 | 19:19:12 | "Um, can you tell me what time the daylight savings..." (same) | 121 | 1881 | 176 |

## Answers to the five design open questions

### 1. Does `body.user` get populated by the glasses?

**NO.** `user: '<absent>'` in every turn. The glasses never populate the OpenAI `user` field.

**Production implication:** Server-side session key MUST be derived from something else. Candidate: client IP (or Tailscale hostname, since the user is routing over Tailscale — `100.108.145.63` is a Tailscale CGNAT IP). For single-user setups this is fine; for multi-user production deployments we'd need a different strategy (TBD if that ever matters).

### 2. Does `messages[]` grow turn-by-turn?

**NO.** Every turn contains exactly one message — the current user utterance. The glasses do NOT maintain conversation history client-side.

**Production implication:** Confirms **Option B** (server-side history). The production `bridge-server/` MUST:
- Maintain a per-session conversation history (keyed by client IP for v1)
- Reconstruct the full `messages[]` (system + history + new user msg) before forwarding to LiteLLM
- Apply a max-history-turns cap (current `MAX_HISTORY_TURNS=10` is reasonable)
- Without this, the user experiences total amnesia across turns

### 3. Any unexpected headers or body fields?

**Headers — all consistent across 5 turns:**
```
user-agent: Dart/3.11 (dart:io)              ← NOT Dart/3.8 from the blog; Even Hub v0.0.12 uses Dart 3.11
accept-encoding: gzip                         ← NEW: glasses want gzip-compressed responses!
content-length: <N>
host: your-host.your-tailnet.ts.net:8765   ← Tailscale MagicDNS hostname
authorization: Bearer probe-test-token        ← user-configured token, sent verbatim as Bearer
content-type: application/json
x-openclaw-agent-id: main                     ← lowercase, always "main"
```

**Body fields — only `model` and `messages`. No `user`, no `stream`, no `temperature`, no other OpenAI fields.** Minimal client.

**New findings vs prior research:**
- `User-Agent` upgraded from `Dart/3.8` to `Dart/3.11` in current Even Hub
- `accept-encoding: gzip` is sent — production should compress responses (significant bandwidth saving on longer replies)
- No `Accept` header restricting response media types — gives us flexibility on response `Content-Type`

### 4. Real end-to-end latency distribution?

| Phase | Latency |
|---|---|
| Cold start (first request after server idle) | **17.3 seconds** |
| Warm (subsequent requests, same model) | 1.7–2.3 seconds |

**The 17s cold start is the killer finding.** The dAAAb Cloudflare Worker sets a 22s timeout budget assuming that's safe; this real measurement shows 17s for a small 4B model on the user's hardware. Larger models (Claude, GPT-4) over LiteLLM could easily exceed this on cold start.

**Production implications:**
- Response budget is much tighter than 22s in practice. Sub-10s is the realistic target.
- Cold-start mitigation is a real concern — keep-alive pings to LiteLLM, model preloading, or accept occasional glasses-side timeout
- Server should log latency per request and alert if consistently over 10s

### 5. User-Agent value and casing?

`User-Agent: Dart/3.11 (dart:io)` — confirmed consistent across all 5 turns. Lowercase `user-agent` header name. Updated from the blog's `Dart/3.8` — Even Hub has moved to Dart 3.11 by v0.0.12.

**Production implication:** Use this as the glasses-traffic identifier in logs. Don't rely on it for auth (trivially spoofable).

## NEW findings not in the original five questions

### 🔥 N1: Glasses fire DUPLICATE PARALLEL REQUESTS

This is the most significant unexpected finding. Look at the timestamps:

```
19:18:57 TURN 2 — "What time is it?" — 2320ms
19:18:57 TURN 3 — "What time is it?" — 1815ms   ← SAME SECOND, SAME CONTENT
19:19:12 TURN 4 — "Um, can you tell me..." — 1704ms
19:19:12 TURN 5 — "Um, can you tell me..." — 1881ms   ← SAME SECOND, SAME CONTENT
```

Two interpretations:
- **(a) Built-in redundancy:** Glasses fire each utterance twice in parallel as a reliability hedge. First response wins; the other is discarded.
- **(b) Slow-response retry:** Glasses fire once, don't get a reply within some short window (~1s? ~2s?), and retry. The original eventually completes too.

For TURN 1 (17s cold start), there were three total attempts — TURN 1 alone at 19:17:59, then TURN 2+3 in parallel at 19:18:57 (~40s later). The 40s gap suggests the user manually re-prompted after the first timed out, and the re-prompt fired duplicates.

For TURN 4+5 (warm, fast), the duplicate fires essentially simultaneously with the original. This favors interpretation **(a) — built-in parallel redundancy**.

**Production implications — CRITICAL:**
- Server will receive 2+ requests per user utterance. Each will hit LiteLLM independently unless we deduplicate.
- **Must implement request deduplication:** hash on (client_ip, messages_content, recent_time_window) and return a cached response for duplicates within e.g. 5 seconds.
- Without deduplication, every user utterance costs 2x LLM calls. With a small Qwen3.5-4B that's tolerable; with Claude Sonnet it doubles cost and load.
- Must be careful: if the user legitimately says the same thing twice in a row ("what time is it? ... what time is it?"), we shouldn't dedupe the second one. Time window is the disambiguator.

### 🔥 N2: Cold-start latency may cause perceived "first utterance failure"

The 17.3s cold start on TURN 1 is long enough that the user probably saw a timeout or error on the HUD, then re-prompted. The re-prompt (TURN 2+3) hit a warm model and succeeded quickly.

**Production implication:** Consider a startup-time "prewarm" — send a tiny test request to LiteLLM on server boot to load the model before the first real glasses request arrives. Or accept the cold-start tax and document it.

### ✅ N3: Tailscale works as the transport

The user successfully routed glasses traffic over Tailscale (`your-host.your-tailnet.ts.net`). The phone's Tailscale config is using the system trust store, which is why the user's local CA cert validated fine. This means:
- Production doesn't need to be LAN-only — any IP-reachable network works
- Tailscale MagicDNS hostnames are accepted by Even Hub in the Add Agent URL field
- No need to expose the server directly to the internet; Tailscale (or similar VPN/overlay) is a viable remote-access path

### ✅ N4: `model: "openclaw"` is hardcoded, exactly as documented

Every request has `"model":"openclaw"`. No variation. Production can ignore this field entirely (or use it as a sanity-check assertion).

### ✅ N5: Auth is exactly as documented

`Authorization: Bearer probe-test-token` — the token the user entered in Even Hub → Add Agent is sent verbatim with `Bearer ` prefix. No transformation, no hashing, no additional auth headers. Production auth check is `Authorization == f"Bearer {BYOA_TOKEN}"`.

### ❓ N6: Open questions still unanswered by this spike

Per the design, the following remain unknown (deferred to a v2 probe if needed):
- **HUD error rendering for 401/500/timeout** — user did not report what the HUD showed during TURN 1's 17s wait. Need to probe with deliberate error responses.
- **SSE streaming tolerance** — glasses sent `accept-encoding: gzip` but no `Accept: text/event-stream`. Unknown whether they'd consume SSE if we returned it. Production v1 ships non-streaming; SSE is a possible v2 optimization if first-token-latency matters.
- **Character limit before HUD truncation** — TURN 4 returned 188 chars and TURN 5 returned 176 chars; user did not report truncation. Need to push 500/1000/5000-char responses to find the limit.

## Production migration decisions locked in by these observations

These observations close out the major design questions for the future `byoa-protocol-migration` change:

| Decision | Locked in by | Value |
|---|---|---|
| History ownership | Q2 | Server-side, keyed by client IP |
| Session key strategy | Q1 | Client IP (or Tailscale hostname) — `body.user` is not available |
| Wire format | N4, N5 | Plain JSON chat-completion; `model` ignored; Bearer auth |
| Response compression | N3 (accept-encoding: gzip) | Production SHOULD gzip responses |
| Request deduplication | 🔥 N1 | **MUST** dedupe by (client_ip, content_hash, ~5s window) — otherwise 2x LLM cost |
| Cold-start mitigation | 🔥 N2 | SHOULD prewarm LiteLLM on server boot |
| Response budget | Q4 | Target sub-10s; treat 17s+ as failure-prone |
| SSE in v1 | N6 | NO — ship non-streaming first |
| User-Agent in logs | Q5 | `Dart/3.11 (dart:io)` identifies glasses traffic |

## Spike verdict

**The spike succeeded.** All five original open questions answered, plus three significant new findings (duplicate requests, cold-start budget, gzip support) that materially shape the production migration. The probe server is working correctly and can be disposed of after the production change is implemented.
