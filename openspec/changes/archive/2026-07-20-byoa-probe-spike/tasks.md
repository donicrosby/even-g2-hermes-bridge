## 1. Scaffold probe package

- [x] 1.1 Create `probe/src/byoa_probe/` package directory with empty `__init__.py` (uv src layout per `AGENTS.md`)
- [x] 1.2 Create `probe/pyproject.toml` declaring `[project]` with `name = "byoa-probe"`, `version = "0.1.0"`, `requires-python = ">=3.11"`, `[project.dependencies]` pinning `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`, `httpx>=0.27.0`, and `[build-system]` using `uv_build>=0.11.25,<0.12` as the build backend (per `AGENTS.md`)
- [x] 1.3 Run `uv sync` inside `probe/` to generate `probe/.venv/` and `probe/uv.lock`; commit `uv.lock`, gitignore `.venv/`
- [x] 1.4 Verify `uv run python -c "import fastapi, uvicorn, httpx; print('ok')"` succeeds from the `probe/` directory
- [x] 1.5 Add `probe/.gitignore` with `.venv/`, `probe.log`, `__pycache__/`

## 2. Probe server core (POST / handler)

- [x] 2.1 Create `probe/server.py` with FastAPI app and `@app.post("/")` handler matching `specs/byoa-probe/spec.md` Requirement "Probe server accepts BYOA POST requests"
- [x] 2.2 Implement Bearer-accepted-not-enforced auth: read `Authorization` header, log verbatim, never reject (probe is observation-only)
- [x] 2.3 Implement body parsing: read raw bytes first (for verbatim log), then attempt JSON parse; on parse failure return 400 with `{"error":{"message":"invalid JSON","type":"invalid_request_error"}}` per spec
- [x] 2.4 Implement request logging entry creation — gather: turn counter (module-level, monotonically increasing), ISO timestamp, client IP/port, method, path, all headers preserving original casing, raw body, parsed body (model value, `user` field presence/value, message role+first-200-chars, any other fields)
- [x] 2.5 Implement LiteLLM forwarding per spec Requirement "Probe server forwards to LiteLLM with model rewrite": build new body with `model` rewritten to `CHAT_MODEL` env var, prepend `{role:"system",content:SYSTEM_PROMPT}` if no system message already present, force `stream:false`, POST to `{LITELLM_BASE_URL}/v1/chat/completions` with `Authorization: Bearer {LITELLM_API_KEY}` via `httpx.AsyncClient`
- [x] 2.6 Implement LiteLLM error handling: on non-2xx from LiteLLM, log status + body, return HTTP 200 to glasses with `choices[0].message.content = "[probe] LiteLLM error: <status> <first 120 chars>"` so HUD renders something useful
- [x] 2.7 Implement response builder per spec Requirement "Probe server returns OpenAI chat-completion JSON": return `{"id":"g2-probe-<uuid8>","object":"chat.completion","created":<ts>,"model":"g2-probe","choices":[{"index":0,"message":{"role":"assistant","content":"<reply>"},"finish_reason":"stop"}],"usage":{"prompt_tokens":0,"completion_tokens":<len>,"total_tokens":<len>}}` with LiteLLM's `content` copied verbatim (NO truncation — we want to observe glasses-side truncation behavior)
- [x] 2.8 Handle LiteLLM non-JSON response (e.g. SSE returned by mistake): log raw, return HTTP 200 with `choices[0].message.content = "[probe] LiteLLM returned non-JSON response"`, no crash

## 3. Logging infrastructure

- [x] 3.1 Implement turn-counter as module-level integer starting at 1, incremented per POST
- [x] 3.2 Implement `write_log(entry)` that appends the structured plain-text block (per design D7 format) to BOTH `probe.log` (append mode, creates if missing) AND stdout; `=== TURN N — <ISO ts> ===` ... `=== END TURN N ===` delimiters
- [x] 3.3 Verify the log entry includes ALL required fields from spec: client IP:port, method, path, headers (verbatim casing), raw body, parsed body (model, user presence+value, messages summary, other fields), forwarded LiteLLM request (rewritten model, message count), LiteLLM response (status, content char count, latency_ms)
- [x] 3.4 Verify `probe.log` is created on first write when it doesn't exist (do not pre-create)

## 4. TLS enforcement at startup

- [x] 4.1 Implement startup check: refuse to start unless `SSL_CERT_FILE` and `SSL_KEY_FILE` are both set to existing readable paths; print clear error message naming the missing/invalid variable
- [x] 4.2 Wire `uvicorn.run()` to use `ssl_certfile=SSL_CERT_FILE`, `ssl_keyfile=SSL_KEY_FILE`
- [x] 4.3 Verify there is NO plaintext fallback path — the server only ever starts in HTTPS mode

## 5. Health endpoint

- [x] 5.1 Implement `GET /health` returning `{"status":"ok","mode":"probe","upstream":"<LITELLM_BASE_URL>","chat_model":"<CHAT_MODEL>"}` with HTTP 200
- [x] 5.2 Verify `/health` does NOT write a turn entry to `probe.log`

## 6. Env vars and configuration

- [x] 6.1 Create `probe/.env.example` with all required + optional vars: `LITELLM_BASE_URL=`, `LITELLM_API_KEY=`, `CHAT_MODEL=`, `SYSTEM_PROMPT=`, `SSL_CERT_FILE=`, `SSL_KEY_FILE=`, `HOST=0.0.0.0`, `PORT=8765`, `LOG_LEVEL=INFO`
- [x] 6.2 Read all env vars at import time with `os.getenv` and sensible defaults (matching `bridge-server/.env.example` conventions where applicable)
- [x] 6.3 Fail fast on missing required vars: `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `CHAT_MODEL`, `SSL_CERT_FILE`, `SSL_KEY_FILE` must all be non-empty or the server refuses to start with a clear message

## 7. README

- [x] 7.1 Create `probe/README.md` with sections: Prerequisites (uv, local CA cert + key, LiteLLM reachable), Setup (`uv sync`), Configure (`cp .env.example .env` + edit), Run (`uv run uvicorn probe.server:app --host 0.0.0.0 --port 8765 --ssl-certfile $SSL_CERT_FILE --ssl-keyfile $SSL_KEY_FILE`), Configure Even Hub (Settings → Add Agent → Name: Probe, URL: `https://<LAN-IP>:8765`, Token: anything), Probe Steps (utterance 1: "my name is Don", utterance 2: "what's my name?", utterance 3: "what's 2+2?"), What To Look For (turn 1 vs turn 2 messages[] diff, body.user presence, latency_ms, response char count), Mapping To Production Decisions (each observation → which byoa-protocol-migration decision it informs)
- [x] 7.2 Verify README contains literal `uv run` commands, not `pip install` or venv-activation instructions

## 8. End-to-end smoke (no glasses required)

- [x] 8.1 Start the probe server locally with valid TLS env vars; `curl -k https://localhost:8765/health` returns 200 with expected JSON
- [x] 8.2 POST a fake glasses request: `curl -k -X POST https://localhost:8765/ -H "Authorization: Bearer fake-g2-token" -H "Content-Type: application/json" -H "x-openclaw-agent-id: main" -d '{"model":"openclaw","messages":[{"role":"user","content":"hello"}]}'` — returns 200 with chat-completion JSON containing LiteLLM's actual reply (or friendly error if LiteLLM is unreachable)
- [x] 8.3 Inspect `probe.log` — verify TURN 1 entry has all required fields, correct delimiter format, verbatim header casing, parsed body details
- [x] 8.4 Send a second POST with the same body; verify TURN 2 entry appears with incremented counter, and that the log shows whether messages[] grew or stayed single-message (this simulates the multi-turn behavior we want to observe on hardware, with us acting as the glasses)
- [x] 8.5 Stop server, confirm `probe.log` persists, confirm no files outside `probe/` and `openspec/changes/byoa-probe-spike/` were modified (`git status` clean outside those paths)

## 9. Real-hardware probe (capture observations)

- [x] 9.1 On the phone, open Even app → Settings → Add Agent; create entry pointing at `https://<probe-LAN-IP>:8765` with any token; verify it saves (this itself tells us whether Even Hub accepts LAN HTTPS with a local-CA cert)
- [x] 9.2 With glasses connected, long-press touchbar and say: "my name is Don" — wait for HUD reply; capture what the HUD shows
- [x] 9.3 Long-press and say: "what's my name?" — wait for HUD reply; capture what the HUD shows
- [x] 9.4 Long-press and say: "what's 2+2?" — wait for HUD reply
- [x] 9.5 Stop the server and copy `probe.log` contents into the OpenSpec change directory as `observations.md` (not a spec — a finding artifact for the next change)
- [x] 9.6 In `observations.md`, answer the five open questions from design.md: (1) does `body.user` get populated? (2) does `messages[]` grow turn-by-turn? (3) any unexpected headers/fields? (4) end-to-end latency distribution? (5) User-Agent casing/values?
- [x] 9.7 Note any glasses-side anomalies: HUD truncation length, error rendering for any failed responses, behavior on long responses, multi-turn "amnesia" present or not

## 10. Spike closure

- [x] 10.1 Confirm all spec scenarios verifiable without real hardware (sections 1–8) pass
- [x] 10.2 Confirm `observations.md` answers all five design open questions OR documents which questions need a follow-up spike (SSE tolerance, HUD error rendering, char limits — deferred per design)
- [x] 10.3 Verify `git status` shows only `probe/` files, `openspec/changes/byoa-probe-spike/` files, and the new `AGENTS.md` — nothing else touched
- [x] 10.4 Mark the change ready for archive (do NOT archive yet — the observations feed the next change, `byoa-protocol-migration`, which should reference this spike's findings)
