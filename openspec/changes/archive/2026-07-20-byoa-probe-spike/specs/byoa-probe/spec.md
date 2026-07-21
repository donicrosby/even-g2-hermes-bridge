## ADDED Requirements

### Requirement: Probe server accepts BYOA POST requests

The probe server SHALL accept HTTP POST requests at the root path (`/`) with `Content-Type: application/json` and a JSON body conforming to the OpenAI chat-completions request shape (containing at minimum a `messages` array). The server SHALL NOT enforce Bearer authentication during the probe — any `Authorization` header value SHALL be accepted and logged verbatim so the glasses' actual token format is observable.

#### Scenario: Glasses send a valid chat-completion request

- **WHEN** the G2 glasses (Even Hub "Add Agent" mode) POST to `https://<probe-host>:<probe-port>/` with `Content-Type: application/json`, `Authorization: Bearer <some-token>`, and body `{"model":"openclaw","messages":[{"role":"user","content":"what time is it"}]}`
- **THEN** the probe server accepts the request without rejecting auth, logs the full request (headers with original casing, raw body, parsed body, client IP, ISO timestamp, turn number), and proceeds to forward to LiteLLM

#### Scenario: Glasses send a request with unexpected fields

- **WHEN** the glasses POST a body containing fields beyond `model` and `messages` (for example `user`, `stream`, `temperature`, or unknown fields)
- **THEN** the probe server logs every field verbatim and proceeds to forward to LiteLLM, without rejecting the request

#### Scenario: Glasses send malformed JSON

- **WHEN** the glasses POST a body that fails JSON parsing
- **THEN** the probe server logs the raw body string, returns an HTTP 400 with an OpenAI-style error body `{"error":{"message":"invalid JSON","type":"invalid_request_error"}}`, and does not forward to LiteLLM

### Requirement: Probe server forwards to LiteLLM with model rewrite

The probe server SHALL forward each accepted request to `{LITELLM_BASE_URL}/v1/chat/completions` using HTTP POST with `Content-Type: application/json` and `Authorization: Bearer {LITELLM_API_KEY}`. The forwarded body SHALL be the glasses' `messages` array with the `model` field rewritten from `"openclaw"` (or whatever the glasses sent) to the value of the `CHAT_MODEL` environment variable. A system message with `role: "system"` and `content: {SYSTEM_PROMPT}` SHALL be prepended to the `messages` array if and only if no system message is already present in the incoming request. The `stream` field SHALL be set to `false` in the forwarded request regardless of what the glasses sent.

#### Scenario: Glasses send model="openclaw"

- **WHEN** the glasses send `{"model":"openclaw","messages":[{"role":"user","content":"hi"}]}`
- **THEN** the probe server forwards to LiteLLM with body `{"model":"<CHAT_MODEL>","messages":[{"role":"system","content":"<SYSTEM_PROMPT>"},{"role":"user","content":"hi"}],"stream":false}`

#### Scenario: Glasses already include a system message

- **WHEN** the glasses send `{"model":"openclaw","messages":[{"role":"system","content":"my system"},{"role":"user","content":"hi"}]}`
- **THEN** the probe server forwards to LiteLLM with the glasses' system message preserved, NOT prepended with the env SYSTEM_PROMPT, and with `model` rewritten and `stream:false` added

#### Scenario: LiteLLM returns an error

- **WHEN** LiteLLM responds with a non-2xx status code (e.g. 401, 404, 500, 503)
- **THEN** the probe server logs the LiteLLM status code and response body, and returns an HTTP 200 to the glasses with a chat-completion response whose `choices[0].message.content` is a short friendly error message like `"[probe] LiteLLM error: <status> <first 120 chars of body>"` so the HUD renders something useful and the user knows the upstream is broken

### Requirement: Probe server returns OpenAI chat-completion JSON

The probe server SHALL return HTTP 200 with `Content-Type: application/json` and a body matching the OpenAI chat-completion response shape: `{"id","object":"chat.completion","created","model","choices":[{"index":0,"message":{"role":"assistant","content":"<reply>"},"finish_reason":"stop"}],"usage":{"prompt_tokens","completion_tokens","total_tokens"}}`. The `id` field SHALL be a unique string prefixed with `g2-probe-`. The `model` field SHALL be the literal string `"g2-probe"`. The `content` SHALL be the full assistant text returned by LiteLLM (no truncation in the probe — we want to observe whether the glasses truncate or error on long replies).

#### Scenario: LiteLLM returns a normal reply

- **WHEN** LiteLLM returns a 2xx with a standard chat-completion body containing `choices[0].message.content`
- **THEN** the probe server returns HTTP 200 with the canonical probe response shape, copying LiteLLM's `content` verbatim into `choices[0].message.content`

#### Scenario: LiteLLM returns a streaming-shaped response despite stream=false

- **WHEN** LiteLLM returns a response that is not parseable as a single chat-completion JSON object (e.g. an SSE stream by mistake)
- **THEN** the probe server logs the raw response, returns an HTTP 200 to the glasses with `choices[0].message.content` set to `"[probe] LiteLLM returned non-JSON response"`, and does not crash

### Requirement: Probe server logs every request to probe.log and stdout

The probe server SHALL write a structured plain-text log entry for every received POST to both `probe.log` (in the working directory, append-only) and stdout. Each entry SHALL be delimited by `=== TURN N — <ISO timestamp> ===` and `=== END TURN N ===` markers, where N is a monotonically increasing per-process counter starting at 1. The entry SHALL include: client IP and port, HTTP method, path, all request headers with original casing and values (including the `Authorization` value verbatim — this is a probe, masking is not useful), the raw request body, the parsed body (model value, presence and value of `user` field, count and per-message role+first-200-chars-of-content for messages, any other fields), the forwarded LiteLLM request (rewritten model, message count), and the LiteLLM response (status, content character count, latency in milliseconds).

#### Scenario: First request after server start

- **WHEN** the probe server receives its first POST
- **THEN** the log entry begins with `=== TURN 1 — <ISO timestamp> ===` and the counter is 1

#### Scenario: Subsequent requests

- **WHEN** the probe server receives subsequent POSTs
- **THEN** the counter increments by 1 per request and the timestamp reflects when the request was received

#### Scenario: Log file does not exist

- **WHEN** the probe server starts and `probe.log` does not exist in the working directory
- **THEN** the server creates the file on first write (append mode) without error

### Requirement: Probe server starts only with valid TLS configuration

The probe server SHALL require `SSL_CERT_FILE` and `SSL_KEY_FILE` environment variables to be set to valid filesystem paths before startup. If either is unset or points to a non-existent file, the server SHALL refuse to start with a clear error message indicating which variable is missing. The server SHALL NOT offer a plaintext fallback mode — HTTPS is mandatory for the probe to accurately reflect the production target.

#### Scenario: Both SSL env vars set to valid paths

- **WHEN** `SSL_CERT_FILE` and `SSL_KEY_FILE` are both set to paths that exist and are readable
- **THEN** the server starts successfully and logs `Bridge probe starting in TLS mode on https://<HOST>:<PORT>`

#### Scenario: SSL_CERT_FILE missing

- **WHEN** `SSL_CERT_FILE` is unset or empty and `SSL_KEY_FILE` is set
- **THEN** the server refuses to start and prints `Refusing to start: SSL_CERT_FILE and SSL_KEY_FILE must both be set for HTTPS. Got cert='<value>', key='<value>'.`

#### Scenario: SSL key file path does not exist

- **WHEN** `SSL_KEY_FILE` is set to a path that does not exist on the filesystem
- **THEN** the server refuses to start and prints `Refusing to start: SSL_KEY_FILE path '<path>' does not exist.`

### Requirement: Probe server exposes a health endpoint

The probe server SHALL expose `GET /health` returning `{"status":"ok","mode":"probe","upstream":<LITELLM_BASE_URL>,"chat_model":<CHAT_MODEL>}` with HTTP 200. This endpoint SHALL NOT log a turn entry — it is for the user to confirm the server is running and to verify env var resolution before pointing the glasses at it.

#### Scenario: Health check before any glasses traffic

- **WHEN** the user curls `https://<probe-host>:<probe-port>/health` before any glasses POSTs
- **THEN** the server returns 200 with the JSON shape above and the `probe.log` contains no turn entries

### Requirement: Probe server is documented in probe/README.md

The probe directory SHALL contain a `README.md` explaining: how to install dependencies, how to set required env vars (with reference to the user's existing local CA), how to start the server, how to configure the Even Hub "Add Agent" entry to point at it, what utterances to say for the multi-turn test, what to look for in `probe.log`, and which production-migration decisions each observation informs.

#### Scenario: User follows the README from a cold start

- **WHEN** the user opens `probe/README.md` with no prior context
- **THEN** the README contains step-by-step instructions sufficient to: install deps, set env vars, start the server, configure Even Hub, run the multi-turn probe, and locate the observations in `probe.log`

### Requirement: Probe code is isolated from production code

The probe server SHALL be fully contained in the `probe/` directory at the repo root. The probe SHALL NOT import from or modify `bridge-server/`, `glasses-app/`, `app.json`, `docker-compose.yml`, `.env`, or any other production file. The probe SHALL have its own `pyproject.toml` (declaring dependencies under `[project.dependencies]` and managed via `uv`) and `.env.example`, in accordance with the repo-root `AGENTS.md` Python tooling policy. The probe's `pyproject.toml` MAY pin the same package versions as `bridge-server/requirements.txt` for consistency but SHALL be a separate file. The probe SHALL also commit a `uv.lock` alongside `pyproject.toml`.

#### Scenario: Cleanup after the spike

- **WHEN** the user deletes the `probe/` directory after the spike
- **THEN** no other file in the repository is affected and `git status` shows only the deletion of `probe/` and the change directory under `openspec/changes/byoa-probe-spike/`
