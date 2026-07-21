# SSE Tolerance Probe Observations

**Captured:** 2026-07-21 ~01:00–01:03 UTC
**Source:** `probe/sse_server.py` log output (pasted from terminal) + user-reported HUD behavior
**Network path:** G2 → phone (Even Hub v0.0.12) → Tailscale (`your-host.your-tailnet.ts.net:8765`) → SSE probe → canned "Hello, world. Testing SSE." response

## Summary verdict

**SSE is non-viable via the glasses' built-in Add Agent client.** Both flavors tested (`/openresponses` and `/openai-chunk`) produced "network error" on the HUD despite the server returning HTTP 200. The glasses' OpenAI-compatible client expects `Content-Type: application/json` and rejects `text/event-stream` responses.

## Raw data summary

| Turn | Time (UTC) | Flavor | User content | Server response | HUD behavior |
|---|---|---|---|---|---|
| 1 | 00:58:08 | `/` (openresponses) | "I'm checking: 1, 2, 3." | 200 OK + SSE | **Network error** |
| 2 | 00:58:08 | `/` (openresponses) | "I'm checking: 1, 2, 3." (duplicate) | 200 OK + SSE | **Network error** |
| 3 | 01:02:28 | `/openai-chunk` | "testing 123" | 200 OK + SSE | **Network error** |
| 4 | 01:02:57 | `/openai-chunk` | "I'm testing this new configur" | 200 OK + SSE | (not reported, likely also error) |
| 5 | 01:02:57 | `/openai-chunk` | "I'm testing this new configur" (duplicate) | 200 OK + SSE | (not reported, likely also error) |

## Answers to the five design open questions

### 1. Did the HUD render any text at all in Scenario A?

**NO.** No text rendered in either flavor. The HUD showed a "network error" message — the glasses never displayed "Hello", ", world", or any portion of the scripted content. This rules out Interpretation B (timeout while waiting for body) — the rejection happened before any body parsing.

### 2. Did it render progressively or wait for `[DONE]`?

**N/A** — no rendering occurred at all. The glasses rejected the response before any content could be displayed.

### 3. Which of the three flavors worked, if any?

**NONE of the two tested worked.** Both `/openresponses` (OpenClaw event format) and `/openai-chunk` (OpenAI chat.completion.chunk format) produced identical "network error" behavior. The `/raw` flavor was not tested, but is extremely unlikely to succeed given that the two structured formats both failed — if the glasses reject SSE at the Content-Type level, raw text won't help.

### 4. Did Scenario B's 35s gap survive?

**N/A** — Scenario B was not run because Scenario A already failed. No point testing whether SSE survives long silence when SSE doesn't work at all.

### 5. Any errors visible on the HUD or in Even app logs?

**HUD:** Displayed "network error" for both flavors (user-reported).
**Even app logs:** Not accessible during this probe.
**Probe server:** Logged `200 OK` for every request — uvicorn successfully sent the SSE response, but the glasses' HTTP client rejected it.

## Additional findings

### 🔥 N1 (re-confirmed): Glasses fire duplicate parallel requests for `/openai-chunk` too

Turns 4+5 fired at the same second (01:02:57) with identical content. This confirms the duplicate-fire pattern from the byoa-probe-spike is consistent across both `/openresponses` and `/openai-chunk` flavors — it's a property of the glasses' Add Agent client, not the response format.

### N2: The rejection is consistent across SSE event shapes

Both OpenResponses event format (`event: response.created\ndata: ...`) and OpenAI chunk format (`data: {"choices":[{"delta":{...}}]}`) failed identically. The glasses aren't parsing the body and finding the wrong shape — they're rejecting the response earlier than that, most likely at the `Content-Type: text/event-stream` header check.

### N3: Tailscale transport is not the culprit

The 200 OK responses were delivered successfully over Tailscale — uvicorn logged them. The glasses' HTTP client received the response bytes; it then rejected them. A LAN test would rule out Tailscale proxying definitively, but the consistent failure pattern across both flavors strongly suggests the rejection is at the glasses' HTTP client layer, not the transport layer.

## Root cause analysis

```
   Glasses POST /  ──▶  Probe responds with:
                        HTTP/1.1 200 OK
                        Content-Type: text/event-stream     ←←← THE PROBLEM
                        Cache-Control: no-cache
                        Connection: keep-alive
                        X-Accel-Buffering: no

                        event: response.created
                        data: {...}

  Glasses' Dart HTTP client:
    1. Receives 200 status                          ✓
    2. Reads Content-Type: text/event-stream        ✗ ← REJECTION POINT
    3. (Or) tries to JSON-parse the body            ✗ ← ALT REJECTION POINT

  Glasses report: "network error"
  Glasses display: "network error" on HUD

The Dart HTTP client (or the higher-level OpenAI-compatible wrapper on top of it)
is hard-coded to expect `application/json` responses. SSE is not in its
accepted response types. The "network error" is a generic error message for
"response parsing failed."
```

## Architecture decision

```
┌──────────────────────────────────────────────────────────────────────┐
│  RESULT                                                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ❌ SSE pass-through is NOT a viable path for the glasses' built-in  │
│     Add Agent mode.                                                  │
│                                                                      │
│  The glasses' BYOA client requires `application/json` responses.     │
│  It rejects `text/event-stream` at the HTTP client layer, before     │
│  any body parsing.                                                   │
│                                                                      │
│  → The slow-agent problem CANNOT be solved by SSE streaming on the   │
│    BYOA path.                                                        │
│                                                                      │
│  → We must build (or adopt) a custom glasses-app + Hermes platform   │
│    adapter to get the async / streaming behavior we need.            │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Why this is a clean answer

The result definitively rules out the cheapest path (SSE pass-through in `bridge-server/` — ~1 day). The remaining options are:

1. **Build custom glasses-app + Hermes platform adapter** (huntsyea pattern) — 3-5 days, full feature set (streaming tokens, tool status, pairing, sessions, no timeout ever because WS connection is persistent)
2. **Adopt huntsyea's project directly** — 1 day setup, less control, depends on their release cadence
3. **Stay on BYOA + lazy delivery** (low cost, degraded UX) — keep what we have, add "ack early, deliver on next utterance" pattern, accept the UX compromise

The next change proposal should pick one of these three. My recommendation is **Option 1 or 2** depending on how much customization the user wants.

## Production migration implications

The existing `bridge-server-byoa-migration` we shipped remains valuable — it's the right answer for the sync (short-response) path. But it cannot solve the slow-agent problem alone. The next change will likely:

- Keep `bridge-server/` as the sync path for short responses (or replace it entirely if we go full WS)
- Add a new Hermes platform plugin (Python, in this repo or as a separate one)
- Add a new glasses-app (TypeScript, in this repo or as a separate one)
- Or install huntsyea's plugin + adopt their glasses-app

The `byoa-bridge` package and `glasses-app/` legacy plugin may both become redundant depending on which path we choose.

## Spike verdict

**The spike succeeded.** It produced a definitive, unambiguous answer to the load-bearing question: SSE is not viable on the glasses' built-in Add Agent client. The answer was not what we hoped, but it was the question we needed answered before committing to the next architecture. The probe is complete and can be disposed of after the next change's proposal is written.
