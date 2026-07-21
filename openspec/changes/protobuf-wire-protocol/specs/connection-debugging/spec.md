## ADDED Requirements

### Requirement: Structured logging on every frame inbound and outbound

The plugin SHALL log every frame it sends or receives at INFO level with structured fields including `direction` (`in` or `out`), `frame_type` (the oneof variant name), `byte_size` (the serialized frame size), and `chat_id` (when known). The log entry SHALL use a structured logger (Python: `structlog`; TypeScript: a tiny `src/log.ts` wrapper that emits JSON-shaped objects). Free-text log messages SHALL NOT be used for frame-level events.

The glasses-app SHALL log every frame inbound and outbound via the same `src/log.ts` wrapper, with `frame_type` and `byte_size` fields. Glasses-app logs flow through `console.log` to the Flutter WebView host log.

#### Scenario: Normal inbound frame
- **WHEN** the plugin receives a valid `Frame` with payload variant `text` from chat_id `g2-1`
- **THEN** the plugin SHALL emit a log entry like `{"event": "frame", "direction": "in", "frame_type": "text", "byte_size": 24, "chat_id": "g2-1"}`
- **AND** the entry SHALL be at INFO level

#### Scenario: Normal outbound frame
- **WHEN** the plugin sends an `assistant_delta` frame to chat_id `g2-1`
- **THEN** the plugin SHALL emit `{"event": "frame", "direction": "out", "frame_type": "assistant_delta", "byte_size": 32, "chat_id": "g2-1"}`
- **AND** the entry SHALL be at INFO level

#### Scenario: Full payload at DEBUG
- **WHEN** the plugin's log level is set to DEBUG
- **THEN** frame log entries SHALL additionally include the decoded payload as a structured `payload` field
- **AND** the payload SHALL be the decoded frame's `MessageToDict` (Python) / `.toJson()` (TypeScript) representation

### Requirement: Connection lifecycle events logged with reason fields

The plugin SHALL log every connection lifecycle event at INFO level. The events SHALL include at minimum: `ws_open` (new connection accepted), `hello_received` (first frame parsed, includes `chat_id`), `auth_check` (with `result: success|failure`), `auth_failed` (with `reason: bad_token|missing_token|malformed_hello|wrong_first_frame`), `registered` (chat_id added to registry), `dispatch_loop_enter`, `dispatch_loop_exit`, `normal_close` (with `code` and `reason`), `abnormal_close` (with `code`, `reason`, and any exception type).

#### Scenario: Successful connection
- **WHEN** a glasses-app connects with a valid token
- **THEN** the plugin SHALL emit, in order: `ws_open`, `hello_received`, `auth_check (success)`, `registered`, `dispatch_loop_enter`
- **AND** each entry SHALL include the `chat_id`

#### Scenario: Auth failure with bad token
- **WHEN** the plugin receives a hello frame with a token that does not constant-time-match `cfg.token`
- **THEN** the plugin SHALL emit `auth_failed` with `reason: bad_token` and the `chat_id` from the hello frame
- **AND** SHALL follow with `abnormal_close` with `code: 1008` and `reason: "unauthorized"`
- **AND** SHALL NOT emit `registered` or `dispatch_loop_enter`

#### Scenario: Malformed hello frame
- **WHEN** the first frame received is not parseable as a `Frame`, or its `payload` is not `hello`
- **THEN** the plugin SHALL emit `auth_failed` with `reason: malformed_hello` or `reason: wrong_first_frame` respectively
- **AND** SHALL close the connection with code 1002

### Requirement: Debug CLI client connects and logs frames

The plugin SHALL ship a CLI tool at `plugin/src/byoa_plugin/debug_client.py` invokable as `uv run python -m byoa_plugin.debug_client`. The CLI SHALL:
1. Accept `--url ws://host:port`, `--token <token>`, and optional `--send <frame-spec>` flags.
2. Connect, send `hello`, and emit a `connected` log entry when `hello.ok` arrives.
3. Log every inbound frame at INFO (frame type + size) and DEBUG (full payload).
4. Send frames specified by `--send` flags (format: `<frame_type>:<arg>`, e.g., `text:hello`, `sessions.list`, `audio.start`).
5. On Ctrl-C, emit a summary of frames sent and received grouped by type, then exit cleanly.

The CLI SHALL use the generated `Frame` stubs for all encoding/decoding (no second wire implementation).

#### Scenario: Connect and observe
- **WHEN** the user runs `uv run python -m byoa_plugin.debug_client --url ws://127.0.0.1:8767 --token $TOKEN`
- **THEN** the CLI SHALL connect, send hello, log `connected` on hello.ok
- **AND** SHALL log every subsequent inbound frame at INFO
- **AND** SHALL continue until Ctrl-C or the server closes the connection

#### Scenario: Send a canned frame
- **WHEN** the user runs `uv run python -m byoa_plugin.debug_client --url ... --token ... --send text:"hello world"`
- **THEN** the CLI SHALL send hello, then send a `Frame` with `payload == text` and `text.content == "hello world"`
- **AND** SHALL continue listening for inbound frames until Ctrl-C

#### Scenario: Reproduce the connect-then-disconnect bug
- **WHEN** the user runs the debug CLI against a bridge instance exhibiting the connect-then-disconnect bug
- **THEN** the CLI SHALL either succeed at the handshake (ruling out server-side handshake bugs) OR fail with a specific error (timeout, auth rejected, malformed reply, etc.)
- **AND** the plugin's structured logs SHALL show the corresponding lifecycle event (e.g., `auth_failed` with reason, or `abnormal_close` with code)
- **AND** together the two log streams SHALL be sufficient to localize the failure

### Requirement: No silent failures

The plugin SHALL NOT silently swallow any decode error, connection close, or malformed frame. Every error path SHALL emit a log entry with: error type, error message, byte count (when relevant), `chat_id` (when known), and the operation that failed (e.g., `parse_frame`, `dispatch`, `send_frame`). The existing pattern of `except Exception: pass` or `except Exception as e: LOG.warning("failed: %s", e)` SHALL be replaced with structured error logs.

#### Scenario: A frame fails to parse
- **WHEN** the plugin receives bytes that fail Protobuf decode
- **THEN** the plugin SHALL log `{"event": "frame_decode_error", "byte_size": N, "chat_id": "...", "error": "...", "first_32_bytes_hex": "..."}`
- **AND** SHALL continue the dispatch loop (does not crash the connection)

#### Scenario: send_frame raises
- **WHEN** the plugin calls `registry.send_frame(chat_id, frame_bytes)` and the underlying WS send raises (e.g., connection closed mid-send)
- **THEN** the plugin SHALL log `{"event": "send_frame_error", "chat_id": "...", "frame_type": "...", "error": "..."}`
- **AND** SHALL clean up the registry entry for that chat_id

#### Scenario: An unknown frame variant arrives
- **WHEN** the plugin receives a `Frame` whose `payload` oneof discriminant is not one of the known variants (e.g., a future client sends a frame type the plugin doesn't know)
- **THEN** the plugin SHALL log `{"event": "unknown_frame_type", "frame_type": "<discriminant>", "chat_id": "..."}`
- **AND** SHALL NOT crash; the dispatch loop SHALL continue
