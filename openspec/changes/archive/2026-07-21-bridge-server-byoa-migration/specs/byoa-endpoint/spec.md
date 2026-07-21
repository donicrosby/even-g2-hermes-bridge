## ADDED Requirements

### Requirement: BYOA request endpoint
The bridge server SHALL expose an HTTP `POST /` endpoint that accepts the Even Hub "Add Agent" BYOA request shape: JSON body with `model` and `messages` fields, where `model` is expected to be `"openclaw"` and `messages` contains the current user message. The server SHALL ignore the incoming `model` for upstream routing and SHALL forward requests to LiteLLM using the configured `CHAT_MODEL`.

#### Scenario: Valid BYOA request
- **WHEN** the glasses POST `/` with `Content-Type: application/json`, `Authorization: Bearer <BYOA_TOKEN>`, `x-openclaw-agent-id: main`, and body `{"model":"openclaw","messages":[{"role":"user","content":"What time is it?"}]}`
- **THEN** the server accepts the request, extracts the latest user message, forwards it to LiteLLM using `CHAT_MODEL`, and returns HTTP 200 with an OpenAI chat-completion response

#### Scenario: Missing user message
- **WHEN** the glasses POST `/` with a valid bearer token but no user message in `messages`
- **THEN** the server returns HTTP 400 with an OpenAI-style error object whose `error.type` is `invalid_request_error`

#### Scenario: Unsupported HTTP method
- **WHEN** a client sends `GET /` or any non-POST method to `/`
- **THEN** the server returns a method-not-allowed response and does not call LiteLLM

### Requirement: Bearer token authentication
The BYOA endpoint SHALL require `Authorization: Bearer <token>` and SHALL accept only requests whose token exactly matches the `BYOA_TOKEN` environment variable. Token comparison MUST use constant-time comparison. The unauthenticated `/health` endpoint SHALL NOT require the token.

#### Scenario: Valid token
- **WHEN** `BYOA_TOKEN=secret-token` and the glasses send `Authorization: Bearer secret-token`
- **THEN** the server processes the request normally

#### Scenario: Missing token
- **WHEN** the request omits the `Authorization` header
- **THEN** the server returns HTTP 401 with an OpenAI-style error object whose `error.type` is `auth_error` and does not call LiteLLM

#### Scenario: Wrong token
- **WHEN** `BYOA_TOKEN=secret-token` and the request sends `Authorization: Bearer wrong-token`
- **THEN** the server returns HTTP 401 with an OpenAI-style error object whose `error.type` is `auth_error` and does not call LiteLLM

### Requirement: Chat-completion response shape
The server SHALL return successful responses in OpenAI chat-completion JSON shape: `id`, `object: "chat.completion"`, `created`, `model`, `choices[0].message.role: "assistant"`, `choices[0].message.content`, `choices[0].finish_reason: "stop"`, and `usage` fields. The server SHALL not stream responses in v1.

#### Scenario: LiteLLM returns assistant content
- **WHEN** LiteLLM returns a successful chat-completion response with `choices[0].message.content = "Hello"`
- **THEN** the bridge returns HTTP 200 and the response body contains `choices[0].message.content = "Hello"` in the same OpenAI chat-completion shape the glasses expect

#### Scenario: LiteLLM returns an upstream error
- **WHEN** LiteLLM returns a non-2xx response or invalid response body
- **THEN** the bridge returns an OpenAI-style error response and does not append the failed turn to conversation history

### Requirement: gzip-compatible responses
The server SHALL support gzip-compressed HTTP responses when the client sends `Accept-Encoding: gzip`. Responses smaller than the configured middleware threshold MAY remain uncompressed.

#### Scenario: Client accepts gzip
- **WHEN** the glasses send `accept-encoding: gzip` and the response body exceeds the gzip threshold
- **THEN** the server returns a valid gzip-compressed response with `Content-Encoding: gzip`

#### Scenario: Client does not accept gzip
- **WHEN** a client omits `Accept-Encoding: gzip`
- **THEN** the server returns an uncompressed JSON response

### Requirement: Health endpoint
The server SHALL expose `GET /health` returning HTTP 200 and JSON containing `status: "ok"`, `mode: "byoa"`, and the configured chat model. The health endpoint SHALL not require BYOA authentication and SHALL not mutate session history or deduplication caches.

#### Scenario: Health check
- **WHEN** docker, Traefik, or a human calls `GET /health`
- **THEN** the server returns HTTP 200 with `status: "ok"` without requiring an `Authorization` header

### Requirement: Upstream TLS uses system trust store
The server SHALL use the system trust store for outbound HTTPS calls to LiteLLM so local-CA-signed certificates installed at the OS level validate automatically. The server SHALL NOT require a separate `SSL_CA_FILE` environment variable.

#### Scenario: LiteLLM uses local CA certificate
- **WHEN** `LITELLM_BASE_URL=https://litellm.local` presents a certificate signed by the user's local CA installed in the system trust store
- **THEN** the bridge's httpx client validates the certificate successfully without `SSL_CA_FILE`

### Requirement: Legacy WebSocket audio protocol removed
The server SHALL remove the legacy `/ws/glasses` WebSocket endpoint, raw PCM audio ingestion, WebRTC VAD, WAV conversion, Whisper STT call, and ad-hoc JSON response frames. The server SHALL no longer depend on `webrtcvad`.

#### Scenario: Legacy WebSocket client connects
- **WHEN** the old `glasses-app/` attempts to open `ws://<server>/ws/glasses`
- **THEN** the server does not accept the WebSocket connection because the endpoint no longer exists

#### Scenario: Dependency list after migration
- **WHEN** dependencies are installed from `bridge-server/pyproject.toml`
- **THEN** `webrtcvad`, `setuptools`, and `python-json-logger` are not installed as direct project dependencies
