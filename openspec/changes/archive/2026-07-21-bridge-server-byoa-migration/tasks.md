## 1. Package migration

- [x] 1.1 Create `bridge-server/pyproject.toml` with `[project]` metadata, dependencies (`fastapi`, `uvicorn[standard]`, `httpx`, `truststore`), and `[build-system]` using `uv_build>=0.11.25,<0.12`
- [x] 1.2 Create `bridge-server/src/byoa_bridge/__init__.py`
- [x] 1.3 Create initial `bridge-server/src/byoa_bridge/server.py` module and move reusable config/logging constants there
- [x] 1.4 Run `uv sync` inside `bridge-server/` to generate `uv.lock` and verify `byoa_bridge` imports
- [x] 1.5 Delete legacy `bridge-server/requirements.txt` after pyproject + uv.lock are working

## 2. BYOA endpoint and auth

- [x] 2.1 Implement `FastAPI` app with `POST /` and `GET /health`
- [x] 2.2 Add required env vars: `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `CHAT_MODEL`, `BYOA_TOKEN`; fail fast with clear errors when missing
- [x] 2.3 Add optional env vars: `SYSTEM_PROMPT`, `MAX_HISTORY_TURNS`, `DEDUP_WINDOW_SECONDS`, `HOST`, `PORT`, `LOG_LEVEL`, `SSL_CERT_FILE`, `SSL_KEY_FILE`
- [x] 2.4 Implement constant-time Bearer auth check using `hmac.compare_digest`; reject missing/wrong token with HTTP 401 OpenAI-style error
- [x] 2.5 Implement BYOA body parsing and validation; accept `model: "openclaw"` but ignore it for upstream model selection
- [x] 2.6 Return HTTP 400 OpenAI-style error for malformed JSON, missing `messages`, or no user message
- [x] 2.7 Return successful replies in OpenAI chat-completion JSON shape with `object: "chat.completion"`, `choices[0].message.role: "assistant"`, and `finish_reason: "stop"`
- [x] 2.8 Add `GZipMiddleware(minimum_size=512)` so glasses requests with `accept-encoding: gzip` get compressed responses when large enough

## 3. LiteLLM forwarding

- [x] 3.1 Add `truststore` import and call `truststore.inject_into_ssl()` before importing `httpx` or `uvicorn`
- [x] 3.2 Implement non-streaming LiteLLM call to `{LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions` with `model: CHAT_MODEL` and `stream: false`
- [x] 3.3 Inject `SYSTEM_PROMPT` as the first message on every forwarded request
- [x] 3.4 Map LiteLLM success into BYOA chat-completion response content
- [x] 3.5 Map LiteLLM HTTP errors, transport errors, and malformed responses into OpenAI-style bridge errors without mutating history
- [x] 3.6 Log upstream status, latency_ms, content length, and model for every LiteLLM call

## 4. Server-side history

- [x] 4.1 Implement per-client in-memory history store keyed by `request.client.host`
- [x] 4.2 Build forwarded messages as `[system] + history + [new_user_msg]`
- [x] 4.3 Append exactly one user/assistant pair after successful LiteLLM completion
- [x] 4.4 Enforce `MAX_HISTORY_TURNS` by discarding oldest complete turns first
- [x] 4.5 Implement `/clear` command: clear only the requesting client IP's history, return chat-completion confirmation, do not call LiteLLM
- [x] 4.6 Add history logs (`history_append`, `history_clear`) with client IP and current turn count, without logging full conversation content

## 5. Request deduplication

- [x] 5.1 Implement dedup key as `(client_ip, sha256(latest_user_content).hexdigest())`
- [x] 5.2 Implement in-flight cache so concurrent duplicate requests await the same `asyncio.Task`
- [x] 5.3 Implement recent-result cache with TTL `DEDUP_WINDOW_SECONDS` (default 5) after completion
- [x] 5.4 Ensure in-flight and recent cache hits do not append history more than once
- [x] 5.5 Add lazy cleanup of expired recent-result cache entries
- [x] 5.6 Validate `DEDUP_WINDOW_SECONDS >= 1` at startup
- [x] 5.7 Log `dedup_new`, `dedup_inflight_hit`, and `dedup_recent_hit` events with client IP and dedup key prefix

## 6. Delete legacy audio/WS implementation

- [x] 6.1 Remove `webrtcvad`, `wave`, `io`, audio frame constants, and VAD settings from server code
- [x] 6.2 Remove `Session` audio-buffer state, `pcm16_to_wav`, `handle_audio_chunk`, `reset_session`, `transcribe`, `chat_stream`, `process_utterance`, and `session_loop`
- [x] 6.3 Remove `@app.websocket("/ws/glasses")` route
- [x] 6.4 Delete `bridge-server/main.py` after `byoa_bridge.server:app` is fully implemented and smoke-tested
- [x] 6.5 Delete `bridge-server/test-client.html`

## 7. Docker and compose

- [x] 7.1 Rewrite `bridge-server/Dockerfile` to use uv, install from `pyproject.toml`/`uv.lock`, copy `src/`, and run `uv run uvicorn byoa_bridge.server:app`
- [x] 7.2 Remove builder-stage gcc/python-dev steps no longer needed after dropping `webrtcvad`
- [x] 7.3 Update Docker healthcheck to hit `http://localhost:8765/health` (container-internal plaintext when TLS is terminated by Traefik)
- [x] 7.4 Update `docker-compose.yml` env vars: remove `WHISPER_MODEL`, VAD vars, and `SSL_CA_FILE`; add `BYOA_TOKEN`, `DEDUP_WINDOW_SECONDS`, `LITELLM_API_KEY`
- [x] 7.5 Remove WebSocket upgrade Traefik middleware labels from `docker-compose.yml`
- [x] 7.6 Keep bridge port `8765:8765` and Traefik HTTP service port 8765 unchanged

## 8. Env examples and docs

- [x] 8.1 Update root `.env.example` with BYOA env vars and remove old WS/audio vars
- [x] 8.2 Update `bridge-server/.env.example` with BYOA env vars and remove old WS/audio vars
- [x] 8.3 Update README architecture section to describe Even Hub Add Agent → POST `/` → bridge-server → LiteLLM
- [x] 8.4 Document Even app configuration: Add Agent URL, token, and expected request/response behavior
- [x] 8.5 Document known limitations: single-user client-IP history, duplicate request dedup window, no SSE in v1, no `glasses-app/` deletion yet

## 9. Verification

- [x] 9.1 Run `uv sync` in `bridge-server/` and verify import: `uv run python -c "import byoa_bridge.server; print('ok')"`
- [x] 9.2 Run `uv build` in `bridge-server/` and verify wheel/sdist build successfully with `uv_build`
- [x] 9.3 Run local uvicorn server and `curl /health` returns HTTP 200 with `mode: "byoa"`
- [x] 9.4 Run authenticated curl POST to `/` with fake BYOA request and real LiteLLM key; verify HTTP 200 chat-completion response
- [x] 9.5 Run unauthenticated POST and wrong-token POST; verify HTTP 401 and no LiteLLM call
- [x] 9.6 Send two concurrent identical requests; verify one LiteLLM call and two successful responses
- [x] 9.7 Send same request again within dedup window; verify cached response and no LiteLLM call
- [x] 9.8 Send `/clear`; verify history clears and LiteLLM is not called
- [x] 9.9 Build Docker image with `docker compose build bridge`
- [x] 9.10 Run `docker compose up -d bridge` and verify `/health` through Traefik route

## 10. Cleanup and handoff

- [x] 10.1 Verify `git status` shows intended changes only: `bridge-server/`, `docker-compose.yml`, env examples, README, and this OpenSpec change
- [x] 10.2 Verify `glasses-app/` is unchanged
- [x] 10.3 Summarize migration result and provide Even Hub Add Agent setup steps for the user
- [x] 10.4 Recommend follow-up cleanup change to delete `glasses-app/` and `probe/` after BYOA production path is stable
