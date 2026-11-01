## MODIFIED Requirements

### Requirement: Hello handshake with token authentication

The server SHALL require the first frame from the client to be a TEXT WebSocket frame containing a JSON `hello` object with a `token` field. The server SHALL validate the token using constant-time comparison (`hmac.compare_digest`) against `EVEN_G2_BRIDGE_TOKEN`. On mismatch or missing token, the server SHALL close the WS connection with code 1008 "unauthorized" and SHALL NOT register the connection.

#### Scenario: Valid token

- **WHEN** the client sends a TEXT WS frame `{"t":"hello","token":"<correct>","device":"g2-serial-123"}`
- **THEN** the server registers the connection under `chat_id="g2-serial-123"`, emits a `hello.ok` frame with `caps`, and keeps the connection open

#### Scenario: Wrong token

- **WHEN** the client sends `{"t":"hello","token":"<wrong>"}`
- **THEN** the server closes the connection with WS code 1008 and does not register the chat_id

#### Scenario: First frame is not JSON text

- **WHEN** the client's first frame is a BINARY WS frame (legacy Protobuf client, version skew)
- **THEN** the server closes the connection with WS code 1002 "expected text frame", logs `auth_failed` with `reason="non_text_first_frame"`, and SHALL NOT crash

#### Scenario: First frame is JSON but not a hello

- **WHEN** the client sends `{"t":"text","text":"hi"}` as the first frame
- **THEN** the server closes the connection with WS code 1002 "expected hello first", logs `auth_failed` with `reason="wrong_first_frame"`

### Requirement: Inbound frame parsing

The server SHALL parse inbound JSON text frames using `json.loads` and dispatch by their `t` field to handlers: `text`, `audio.start`, `audio.stop`, `sessions.list`, `sessions.switch`, `sessions.new`, `stop`. Malformed JSON SHALL be caught and logged; the connection SHALL continue. Unknown `t` values SHALL be logged at WARNING and ignored (not fatal).

#### Scenario: Text frame

- **WHEN** the client sends `{"t":"text","text":"hello"}`
- **THEN** the server constructs a `MessageEvent` and forwards it to the gateway via the platform adapter's message handler

#### Scenario: Unknown frame type

- **WHEN** the client sends `{"t":"future_unknown_type","payload":"test"}`
- **THEN** the server logs a warning with the frame type and continues processing subsequent frames

#### Scenario: Malformed JSON

- **WHEN** the client sends a TEXT WS frame containing `not json{`
- **THEN** the server logs a warning with the parse error, does not crash, and continues processing subsequent frames

#### Scenario: Missing t field

- **WHEN** the client sends a TEXT WS frame `{"foo":"bar"}`
- **THEN** the server logs a warning with "frame missing required 't' field" and continues

### Requirement: Binary audio frames between audio.start and audio.stop

The server SHALL accept BINARY WebSocket frames as raw PCM16 LE 16kHz mono audio when the session is in "audio capturing" state (between `audio.start` and `audio.stop` TEXT frames). Binary frames received outside this state SHALL be ignored with a DEBUG log. There SHALL be no wrapping envelope — raw PCM bytes are accumulated directly into the capture buffer. The accumulated buffer SHALL be capped at `MAX_PCM_BYTES` (8 MiB).

#### Scenario: Audio capture flow

- **WHEN** the client sends `audio.start`, followed by BINARY frames (raw PCM chunks), followed by `audio.stop`
- **THEN** the server accumulates the raw PCM bytes directly, passes them to the ASR module on `audio.stop`, and emits a `transcript` frame with the recognized text

#### Scenario: Binary frame outside audio capture

- **WHEN** the server receives a BINARY frame while `capturing` is `False`
- **THEN** the server logs a DEBUG message and silently drops the frame

#### Scenario: PCM buffer cap enforced

- **WHEN** the accumulated PCM buffer reaches `MAX_PCM_BYTES`
- **THEN** the server stops accumulating, logs a WARNING, and sets `capturing=False`

### Requirement: Outbound frame pushing via connection registry

The server SHALL maintain a `ConnectionRegistry` mapping `chat_id` → active WS connection. The `send_frame(chat_id, frame_str)` method SHALL look up the connection and send the JSON string via `ws.send_str()`. If no connection exists for the given `chat_id`, the method SHALL log a DEBUG message and return `False` without error.

#### Scenario: Push to connected client

- **WHEN** the adapter calls `registry.send_frame("g2-serial-123", '{"t":"assistant.delta","text":"Hi"}')`
- **THEN** the JSON string is sent over the WS connection via `ws.send_str()` registered under "g2-serial-123"

#### Scenario: Push to disconnected client

- **WHEN** the adapter calls `registry.send_frame("g2-serial-123", ...)` but no connection is registered
- **THEN** the method logs `"send_frame: no socket for g2-serial-123"` at DEBUG level and returns `False`