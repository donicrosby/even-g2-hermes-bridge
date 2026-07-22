# glasses-ws-protocol

## Purpose

Defines the server-side WebSocket behavior of the even-g2 Hermes platform plugin: bind host configuration, advertised URL resolution for QR codes and CLI output, hello handshake with constant-time token authentication, inbound frame parsing and dispatch, binary audio buffer management between audio.start and audio.stop, outbound frame pushing via the connection registry, ping keepalive, and delta streaming via StreamState.

## Requirements

### Requirement: WebSocket server binds to configurable host

The plugin SHALL host a WebSocket server bound to the configured `EVEN_G2_BRIDGE_HOST` (default `127.0.0.1`) on `EVEN_G2_BRIDGE_PORT` (default 8767). The plugin SHALL NOT require a specific bind address — `127.0.0.1` (loopback, for Tailscale Serve or same-host reverse proxy), `0.0.0.0` (all interfaces, for direct LAN access or external reverse proxy on a different host), or any specific interface address are all valid.

#### Scenario: Default bind (loopback, for Tailscale or same-host reverse proxy)

- **WHEN** the plugin starts with default config (`EVEN_G2_BRIDGE_HOST=127.0.0.1`)
- **THEN** the WS server binds to `127.0.0.1:8767` and is reachable only from the same host

#### Scenario: Bind to all interfaces (for external reverse proxy on different host)

- **WHEN** `EVEN_G2_BRIDGE_HOST=0.0.0.0` is set
- **THEN** the WS server binds to `0.0.0.0:8767` and is reachable from any host that can route to this machine

#### Scenario: Bind to specific interface

- **WHEN** `EVEN_G2_BRIDGE_HOST=192.168.1.50` is set
- **THEN** the WS server binds to that specific interface only

### Requirement: Advertised URL resolution for QR and CLI output

The plugin SHALL resolve the externally-advertised URL (used in QR codes, `hermes even-g2 qr` output, and dashboard status) using this priority: (1) `EVEN_G2_BRIDGE_PUBLIC_URL` env var if set (explicit override — use as-is), (2) Tailscale MagicDNS URL auto-detected from `tailscale status --json` if Tailscale is available, (3) LAN URL constructed from the bind host. The resolved URL SHALL be logged at startup and exposed via `GET /health` for verification.

#### Scenario: Explicit public URL override

- **WHEN** `EVEN_G2_BRIDGE_PUBLIC_URL=wss://hermes.example.com` is set
- **THEN** the QR generator and CLI output advertise `wss://hermes.example.com?token=<token>` regardless of Tailscale or LAN detection

#### Scenario: Auto-detection from Tailscale

- **WHEN** `EVEN_G2_BRIDGE_PUBLIC_URL` is unset but Tailscale is available and reports MagicDNS name `hermes-host.tailnet-name.ts.net`
- **THEN** the QR generator and CLI output advertise `wss://hermes-host.tailnet-name.ts.net:8443?token=<token>` (using the Tailscale Serve port)

#### Scenario: LAN fallback

- **WHEN** neither `EVEN_G2_BRIDGE_PUBLIC_URL` nor Tailscale is available
- **THEN** the QR generator and CLI output advertise `wss://<lan-ip>:<port>?token=<token>` (with a warning that this URL is only reachable on the LAN and lacks TLS)

### Requirement: Hello handshake with token authentication

The server SHALL require the first frame from the client to be a `hello` JSON frame containing a `token` field. The server SHALL validate the token using constant-time comparison (`hmac.compare_digest`) against `EVEN_G2_BRIDGE_TOKEN`. On mismatch or missing token, the server SHALL close the WS connection with code 1008 "unauthorized" and SHALL NOT register the connection.

#### Scenario: Valid token

- **WHEN** the client sends `{"t":"hello","token":"<correct>","device":"g2-serial-123"}`
- **THEN** the server registers the connection under `chat_id="g2-serial-123"`, emits a `turn.done` or session-ack frame, and keeps the connection open

#### Scenario: Wrong token

- **WHEN** the client sends `{"t":"hello","token":"<wrong>"}`
- **THEN** the server closes the connection with WS code 1008 and does not register the chat_id

### Requirement: Inbound frame parsing

The server SHALL parse inbound JSON frames by their `t` field and dispatch to handlers: `text`, `audio.start`, `audio.stop`, `sessions.list`, `sessions.switch`, `sessions.new`, `stop`. Unknown frame types SHALL be logged and ignored (not fatal).

#### Scenario: Text frame

- **WHEN** the client sends `{"t":"text","text":"hello"}`
- **THEN** the server constructs a `MessageEvent` and forwards it to the gateway via the platform adapter's message handler

#### Scenario: Unknown frame type

- **WHEN** the client sends `{"t":"future_unknown_type",...}`
- **THEN** the server logs a warning with the frame type and continues processing subsequent frames

### Requirement: Binary audio frames between audio.start and audio.stop

The server SHALL accept binary WS frames (raw PCM16 16kHz mono) when the session is in "audio capturing" state (between `audio.start` and `audio.stop` frames). Binary frames received outside this state SHALL be ignored with a debug log.

#### Scenario: Audio capture flow

- **WHEN** the client sends `audio.start`, followed by binary frames, followed by `audio.stop`
- **THEN** the server accumulates the PCM bytes, passes them to the ASR module on `audio.stop`, and emits a `transcript` frame with the recognized text

### Requirement: Outbound frame pushing via connection registry

The server SHALL maintain a `ConnectionRegistry` mapping `chat_id` → active WS connection. The `send_frame(chat_id, frame_str)` method SHALL look up the connection and send the frame. If no connection exists for the given `chat_id`, the method SHALL log a debug message and return without error.

#### Scenario: Push to connected client

- **WHEN** the adapter calls `registry.send_frame("g2-serial-123", '{"t":"assistant.delta","text":"Hi"}')`
- **THEN** the frame is sent over the WS connection registered under "g2-serial-123"

#### Scenario: Push to disconnected client

- **WHEN** the adapter calls `registry.send_frame("g2-serial-123", ...)` but no connection is registered
- **THEN** the method logs `"send_frame: no socket for g2-serial-123"` at debug level and returns

### Requirement: WebSocket ping keepalive

The server SHALL send a WS protocol-level ping (or an SSE-style `: ping\n\n` comment on text channel) every 30 seconds to each connected client to defeat idle proxy timeouts.

#### Scenario: Keepalive during long agent response

- **WHEN** the agent is thinking for 60 seconds and no frames have been sent for 30 seconds
- **THEN** the server sends a ping to the client, resetting any idle-timeout countdown

### Requirement: Delta streaming via StreamState

The server SHALL compute incremental text deltas using `StreamState.delta_for(accumulated_text)` which strips the trailing streaming cursor (` ▉`) and returns only the unsent suffix. The server SHALL emit each non-empty delta as an `assistant.delta` frame.

#### Scenario: First send_message

- **WHEN** `StreamState.sent_len=0` and the adapter receives `send_message(chat_id, "Hello")`
- **THEN** `delta_for("Hello")` returns `"Hello"`, which is emitted as `assistant.delta`

#### Scenario: Subsequent edit_message

- **WHEN** `StreamState.sent_len=5` and the adapter receives `edit_message(chat_id, msg_id, "Hello world")`
- **THEN** `delta_for("Hello world")` returns `" world"`, which is emitted as `assistant.delta`

#### Scenario: Cursor stripped before diffing

- **WHEN** `StreamState.sent_len=11` and the accumulated text is `"Hello world ▉"`
- **THEN** `delta_for` strips ` ▉`, sees `"Hello world"` (len 11 == sent_len), and returns `""` (empty delta, no frame emitted)
