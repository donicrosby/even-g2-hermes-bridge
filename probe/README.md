# BYOA Probe

Temporary diagnostic server that mimics the Even Realities G2 "Add Agent" / BYOA wire protocol, captures raw glasses traffic to `probe.log`, and forwards to your existing LiteLLM so the glasses render real LLM replies end-to-end.

**This is a disposable spike.** Built to resolve five protocol unknowns before the production `bridge-server/` migration. Delete `probe/` when the migration is done.

## Prerequisites

- `uv` installed (the repo-wide Python tooling standard; see `/AGENTS.md`)
- A TLS cert + key pair trusted by your phone (you said you have a working local CA — use it)
- LiteLLM reachable from this machine, with a working API key
- Even Realities G2 + Even Hub app v0.0.7+ (Add Agent support)

## Setup

```bash
cd probe/
uv sync
cp .env.example .env
# Edit .env to point at your LiteLLM + cert paths
```

## Run

From the `probe/` directory:

```bash
uv run --env-file .env uvicorn byoa_probe.server:app \
  --host 0.0.0.0 \
  --port 8765 \
  --ssl-certfile "$SSL_CERT_FILE" \
  --ssl-keyfile "$SSL_KEY_FILE"
```

`uv run --env-file .env` loads your `.env` into the subprocess environment so the server picks up `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `CHAT_MODEL`, `SYSTEM_PROMPT`. The `--ssl-certfile` and `--ssl-keyfile` flags are uvicorn CLI options that must reference the same cert paths via `$SSL_CERT_FILE` / `$SSL_KEY_FILE` — uvicorn does not auto-read those env vars (the server-side TLS is configured via CLI flags, not env, when launched this way).

The server refuses to start unless `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `CHAT_MODEL`, `SSL_CERT_FILE`, and `SSL_KEY_FILE` are all set to non-empty values, and the cert/key paths actually exist on disk. There is no plaintext fallback — the probe runs HTTPS-only to match the production target.

Verify it's up:

```bash
curl -k https://localhost:8765/health
# → {"status":"ok","mode":"probe","upstream":"https://litellm.local","chat_model":"..."}
```

LiteLLM's cert must validate against the system trust store. The server calls `truststore.inject_into_ssl()` at startup, which makes Python's `ssl` module use the OS trust store — so as long as your local CA is installed in the system trust (not just in a PEM file), the upstream connection will validate without any `SSL_CA_FILE` env var.

## Configure the glasses

On your phone, open the **Even** app → **Settings** → **Add Agent** and create an entry:

| Field | Value |
|---|---|
| Name | `Probe` (or anything) |
| URL | `https://<this-machine-LAN-IP>:8765` |
| Token | Any string — the probe accepts any Bearer token and logs it verbatim |

Note whether the Even app lets you SAVE this entry. If it refuses `http://` URLs, that itself is a finding (Even Hub requires HTTPS on LAN). If it accepts and saves, we know LAN HTTPS with a local-CA cert works.

## Probe steps

With glasses connected, long-press the touchbar and say these three utterances in sequence. Pause between each to let the HUD reply fully.

1. **"my name is Don"** — wait for HUD reply
2. **"what's my name?"** — wait for HUD reply
3. **"what's 2+2?"** — wait for HUD reply

Then stop the server with Ctrl+C and open `probe/probe.log`.

## What to look for in `probe.log`

Each turn is delimited by `=== TURN N — <timestamp> ===` and `=== END TURN N ===`. Compare turns to answer these questions:

| Observation | What it tells us | Production decision it informs |
|---|---|---|
| Does `BODY (parsed) → user:` show a value or `<absent>`? | Whether the glasses populate the OpenAI `user` field | If present → use it as session key. If absent → derive session key from client IP or another field. |
| Does `messages:` grow between TURN 1 and TURN 2? | Whether the glasses maintain conversation history client-side, or send only the latest user message | If grows → production can pass `messages[]` straight through to LiteLLM. If stays single → production MUST maintain history server-side. |
| Are there unexpected fields under `other_fields:`? | Any protocol surface we didn't anticipate | informs the production request parser |
| What's `latency_ms` across turns? | Real end-to-end latency for your LiteLLM setup | Tells us if the 22s safe budget (per design) is realistic |
| What's `User-Agent:` value and casing? | Confirms `Dart/3.8 (dart:io)` or notices if Even Hub v0.0.12 changed it | Informs the production logging filter that identifies glasses traffic |
| What's the `Authorization:` value's format? | Confirms Bearer token format the glasses send | Informs the production auth check |

Also note, while probing:

- **HUD truncation length** — for any long LLM reply, observe where (or if) the HUD cuts off. The probe returns the full response with no truncation, so any truncation you see is the glasses' behavior.
- **Multi-turn "amnesia"** — after "my name is Don", does "what's my name?" know your name? If yes → glasses are sending history. If no → glasses are NOT sending history (server-side history required in production).
- **Error rendering** — if any turn fails (LiteLLM unreachable, etc.), what does the HUD show? The probe returns `[probe] LiteLLM error: ...` in the response content; observe how the glasses render it.

## After the probe

Save `probe.log` to `openspec/changes/byoa-probe-spike/observations.md` with answers to the five questions above. Those observations feed the production migration change (`byoa-protocol-migration`, future).

## Cleanup

When the production migration is done:

```bash
rm -rf probe/
```

No other files in the repo are touched by this spike.

---

# SSE Tolerance Probe

A second disposable probe (`sse_server.py`) that always returns `text/event-stream` on POST, designed to answer one load-bearing question: **does the G2's built-in Add Agent client consume SSE?**

The answer determines whether the slow-agent problem (agent thinking > 30s → glasses timeout) can be solved with SSE streaming (cheap, ~1 day) or requires building/adopting a custom glasses-app + Hermes platform adapter (expensive, 3–5 days).

## Prerequisites

Same as the BYOA probe: `uv`, local CA cert + key, Even Hub v0.0.7+ with Add Agent support.

## Configure

The SSE probe uses the same `.env` file as the BYOA probe (it only reads `SSL_CERT_FILE`, `SSL_KEY_FILE`, `HOST`, `PORT`). Default port is **8766** to avoid clashing with the BYOA probe on 8765.

Optional env vars control the scripted event timing:

| Var | Default | Purpose |
|---|---|---|
| `SSE_DELAY_CREATED` | `0` | Delay before `response.created` |
| `SSE_DELAY_IN_PROGRESS` | `2` | Delay before `response.in_progress` |
| `SSE_DELAY_FIRST_DELTA` | `3` | Delay before first token (the "agent is thinking" pause) |
| `SSE_DELAY_BETWEEN_DELTAS` | `0.5` | Delay between successive token deltas |
| `SSE_DELAY_BEFORE_COMPLETED` | `0.5` | Delay before `response.completed` |

## Run — Scenario A (normal timing, ~7s total)

Tests whether the glasses consume SSE **at all**.

```bash
cd probe/
uv run --env-file .env uvicorn sse_server:app \
  --host 0.0.0.0 \
  --port 8766 \
  --ssl-certfile "$SSL_CERT_FILE" \
  --ssl-keyfile "$SSL_KEY_FILE"
```

Verify it's up:

```bash
curl -k https://localhost:8766/health
# → {"status":"ok","mode":"sse-probe","flavors":["/openresponses","/openai-chunk","/raw"],"timing":{...}}
```

### Try each flavor

The probe exposes three SSE flavors because we don't know which format the glasses' OpenAI-compatible client expects. Try them in order:

1. **`/openresponses`** (default; also at `/`) — OpenClaw spec format with `event: response.created`, `event: response.output_text.delta`, etc. Start here.
2. **`/openai-chunk`** — OpenAI `/v1/chat/completions` streaming format with `data: {"choices":[{"delta":{"content":"..."}}]}`. Try if `/openresponses` doesn't render.
3. **`/raw`** — Bare `data: <text>` lines with no event type. Last resort.

**For each flavor:**
1. Point Even app → Add Agent → URL at `https://<LAN-IP>:8766<flavor>` (e.g., `https://your-host.your-tailnet.ts.net:8766/openai-chunk`)
2. Long-press touchbar, say "hello"
3. Wait ~10 seconds (the probe takes ~7s to complete)
4. Observe the HUD: does any text appear? Does it appear progressively, or all at once at the end? Does an error show?

## Run — Scenario B (stress timing, 35s silence)

Tests whether SSE **solves the slow-agent problem** by keeping the glasses alive through a long silence.

```bash
cd probe/
SSE_DELAY_FIRST_DELTA=35 uv run --env-file .env uvicorn sse_server:app \
  --host 0.0.0.0 \
  --port 8766 \
  --ssl-certfile "$SSL_CERT_FILE" \
  --ssl-keyfile "$SSL_KEY_FILE"
```

Use the flavor that worked in Scenario A (or `/openresponses` if none worked). Say "hello" and wait ~40 seconds. Observe:

- Does the HUD survive the 35-second gap between `response.in_progress` and the first token?
- Does it show any "thinking" indicator during the silence?
- Does the glasses-side 30s timeout fire, or does the SSE stream keep it alive?

## What each outcome means

| Result | Architecture decision |
|---|---|
| Scenario A passes (any flavor), Scenario B passes | ✅ SSE pass-through is viable. Next change: add SSE streaming to `bridge-server/` — agent can take minutes, glasses stay alive. |
| Scenario A passes, Scenario B fails (timeout at ~30s) | ⚠️ SSE works but idle-timeout beats us. Need `: ping` keepalive injection or a richer architecture. |
| Scenario A fails on all three flavors | ❌ SSE non-viable. Must build/adopt a custom glasses-app + Hermes platform adapter (huntsyea pattern). |

## After the probe

Save your observations to `openspec/changes/sse-tolerance-spike/observations.md` answering:

1. Did the HUD render any text at all in Scenario A?
2. Did it render progressively (token-by-token) or wait for `[DONE]`?
3. Which of the three flavors worked, if any?
4. In Scenario B, did the HUD survive the 35s gap?
5. Any errors visible on the HUD or in Even app logs?

## SSE Probe Reference

- Proposal: `openspec/changes/sse-tolerance-spike/proposal.md`
- Design: `openspec/changes/sse-tolerance-spike/design.md`
- Spec: `openspec/changes/sse-tolerance-spike/specs/sse-tolerance-probe/spec.md`
- Tasks: `openspec/changes/sse-tolerance-spike/tasks.md`
