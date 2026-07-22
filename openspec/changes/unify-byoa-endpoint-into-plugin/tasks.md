## 1. Config + scaffolding

- [ ] 1.1 Add `byoa_token: str | None` to `BridgeConfig` in `plugin/src/byoa_plugin/config.py`, read from the `BYOA_TOKEN` env var. Default `None` (handler returns 503 when unset).
- [ ] 1.2 Add `BYOA_TOKEN` to `plugin/.env.example` with a placeholder value and comment explaining it's the token Even's Add Agent UI will be configured with.
- [ ] 1.3 Add `origin: Literal['glasses-app', 'byoa'] = 'glasses-app'` keyword arg to `EvenG2Adapter.send_message` and `EvenG2Adapter.edit_message` in `plugin/src/byoa_plugin/adapter.py`. Store on the per-chat_id `StreamState` (or adjacent state) so structured logs can include it.

## 2. POST handler in http_endpoints.py

- [ ] 2.1 In `plugin/src/byoa_plugin/http_endpoints.py`, add a new handler branch for `("POST", "/v1/chat/completions")` in the existing `HttpEndpointHandler` dispatch. Also accept `("POST", "/")` as an alias (matches what Even sends today per the byoa-probe-spike observations).
- [ ] 2.2 Port the OpenAI/OpenClaw request body parsing from `bridge-server/src/byoa_bridge/` — extract `messages[-1].content` (latest user message), validate body shape, raise OpenAI-style error on missing fields.
- [ ] 2.3 Implement bearer-token auth: parse `Authorization: Bearer <token>`, constant-time compare against `cfg.byoa_token`. Return 401 + OpenAI-style error on missing/wrong token. Return 503 if `cfg.byoa_token is None`.
- [ ] 2.4 Implement gzip response support — if request has `Accept-Encoding: gzip` and response body exceeds threshold (~500 bytes), compress the response and set `Content-Encoding: gzip`.
- [ ] 2.5 Implement the call into the adapter: `await adapter.send_message(chat_id='even-add-agent', text=transcript, origin='byoa')`. Block on the full turn completing (the adapter's `edit_message(..., finalize=True)` call signals turn-end).
- [ ] 2.6 Implement the OpenAI chat-completion response formatting: assemble `id`, `object: "chat.completion"`, `created`, `model`, `choices[0].message.{role,content}`, `choices[0].finish_reason: "stop"`, `usage`. Reuse the existing formatter from `bridge-server/` if it's cleanly extractable.

## 3. Tests

- [ ] 3.1 Create `plugin/tests/test_http_endpoints.py` covering:
  - 200 on valid POST + matching token + body with user message
  - 401 on missing token
  - 401 on wrong token (constant-time)
  - 400 on missing user message
  - 405 on GET method
  - 503 when `cfg.byoa_token is None`
  - gzip compression when `Accept-Encoding: gzip` is sent
  - OpenAI-style error body shape on each error path
- [ ] 3.2 Add an integration test in `plugin/tests/test_integration_ws.py` (or a new `test_integration_byoa.py`) that exercises the full flow: POST `/v1/chat/completions` → adapter receives turn with `origin='byoa'` → mock LLM streams a response → `assistant_delta` frames get pushed to a fake connected G2 app → POST handler returns the full chat-completion JSON.
- [ ] 3.3 Add a test verifying the origin tag propagates to structured log entries (assert `extra={'origin': 'byoa'}` appears on the relevant frame logs).
- [ ] 3.4 Run `uv run pytest -q` — verify the full suite still passes (150+ existing + new tests).

## 4. Documentation

- [ ] 4.1 Update `plugin/README.md` with a new "BYOA Setup (Even's Add Agent)" section: configure `BYOA_TOKEN`, configure Even's Add Agent UI with `https://<plugin-host>:<port>/v1/chat/completions`, expected behavior for fast vs. slow responses, troubleshooting.
- [ ] 4.2 Add a "Deprecation Notice" section to `bridge-server/README.md` pointing at the plugin's new endpoint as the preferred BYOA path. Note that `bridge-server/` remains alive for fallback during the transition.
- [ ] 4.3 Update `AGENTS.md` "Path B" section to note that `bridge-server/` is deprecated and the BYOA contract is now served by `plugin/` directly.

## 5. Manual smoke verification (USER RUNS THIS)

- [ ] 5.1 Build the plugin: `cd plugin && uv sync`.
- [ ] 5.2 Set `BYOA_TOKEN` in the plugin's env, restart the plugin (or Hermes Gateway).
- [ ] 5.3 Configure Even's Add Agent UI to point at the plugin's `/v1/chat/completions` endpoint.
- [ ] 5.4 Open the G2 app in the background (so it's connected via WS).
- [ ] 5.5 Say "Hey Even, what's the weather?" — verify fast response shows in Even's overlay; G2 app does not surface.
- [ ] 5.6 Say "Hey Even, write me a 1000-word essay on tulips" — verify G2 app surfaces mid-stream with the response; Even overlay eventually receives the chat-completion (late but correct).
- [ ] 5.7 Capture observations in `openspec/changes/unify-byoa-endpoint-into-plugin/observations.md`.

## 6. PR readiness

- [ ] 6.1 `git diff` review — confirm the change is scoped to `plugin/` (config, http_endpoints, adapter, tests) plus doc updates (README + AGENTS.md). No glasses-app changes, no proto changes, no WS protocol changes.
- [ ] 6.2 Stage and commit atomically per AGENTS.md convention. Suggested commit sequence:
  - `feat(plugin): add BYOA_TOKEN config + origin tagging on adapter turns`
  - `feat(plugin): add POST /v1/chat/completions handler for BYOA surface`
  - `test(plugin): cover BYOA HTTPS handler + integration with adapter`
  - `docs(plugin): BYOA setup section + bridge-server deprecation notice`
- [ ] 6.3 Each commit SHALL independently pass `uv run pytest`, `uv run ruff check`, `uv run basedpyright`. No commit SHALL leave the plugin in a state where the WS path is broken.
