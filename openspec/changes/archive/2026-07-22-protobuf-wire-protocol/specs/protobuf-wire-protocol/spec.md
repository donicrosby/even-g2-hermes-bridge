## ADDED Requirements

### Requirement: Single .proto file is the source of truth for the wire format

The plugin SHALL ship `plugin/proto/hermes_bridge.proto` defining every frame exchanged between the plugin and the glasses-app. The schema SHALL cover all current frame types (8 inbound: hello, text, audio.start, audio.stop, sessions.list, sessions.switch, sessions.new, stop; 11 outbound: hello.ok, assistant.delta, assistant, tool.start, tool.end, turn.done, sessions, active, history, transcript, error) plus one new variant for binary audio (audio_data). The schema SHALL be the authoritative definition of frame shapes; no Python or TypeScript source file SHALL redefine frame fields in parallel.

Rationale: today's `protocol.py` and `protocol.ts` are kept in sync by a codegen hack (`protocol_gen.py`), which has already produced silent drift bugs. A single `.proto` eliminates the parallel-truth problem.

#### Scenario: A new frame type is added
- **WHEN** a contributor wants to add a new `tool_event` frame type
- **THEN** they add a new `ToolEventFrame` message and a new `oneof` variant in `Frame`
- **AND** they run `make proto` (or `buf generate`) to regenerate stubs
- **AND** the new frame type is immediately available in both Python and TypeScript without further code changes

#### Scenario: Schema drift is caught by CI
- **WHEN** a contributor manually edits a generated stub in `plugin/src/byoa_plugin/proto_gen/` instead of changing the `.proto` file
- **THEN** CI (`make proto && git diff --exit-code`) SHALL fail
- **AND** the failure message SHALL direct the contributor to edit the `.proto` file

### Requirement: Frame wire format is a single Protobuf `Frame` with a `oneof payload`

The plugin SHALL define a top-level `Frame` message with a `oneof payload` field covering every frame variant. Every WebSocket message exchanged between plugin and glasses-app SHALL be exactly one serialized `Frame`. Receivers SHALL parse the message as a `Frame` and dispatch on the `payload` discriminator.

```proto
message Frame {
  oneof payload {
    HelloFrame hello = 1;
    // ... 18 more control frame variants ...
    AudioDataFrame audio_data = 99;
  }
}
```

#### Scenario: A normal handshake round-trip
- **WHEN** the glasses-app opens a WS connection and sends a hello frame
- **THEN** the bytes on the wire SHALL be a serialized `Frame` whose `payload` oneof discriminant is `hello`
- **AND** the plugin SHALL parse the bytes into a `Frame`, switch on `WhichOneof("payload") == "hello"`, and read the token from `frame.hello.token`

#### Scenario: A malformed frame arrives
- **WHEN** the plugin receives bytes that do not parse as a valid `Frame` Protobuf message
- **THEN** the parser SHALL raise a typed exception (e.g., `DecodeError`)
- **AND** the plugin SHALL log a warning with the byte count, the chat_id, and the first 32 bytes as hex
- **AND** the plugin SHALL NOT crash; the dispatch loop SHALL continue

### Requirement: Audio PCM is wrapped in an AudioData Frame variant

Binary PCM audio between `audio.start` and `audio.stop` SHALL be wrapped in an `AudioDataFrame { bytes pcm = 1; }` variant of the `Frame` oneof. The plugin SHALL NOT accept raw binary WS messages outside the `Frame` encoding. The `isinstance(raw, bytes)` heuristic in the current `server.py` SHALL be removed.

Rationale: today's heuristic relies on the `capturing` flag being correctly maintained across async tasks; a stray binary frame during a state transition can be misclassified. Wrapping PCM in `AudioData` makes every WS message self-describing.

#### Scenario: Audio capture flow
- **WHEN** the glasses-app sends `audio.start`, then 50 binary audio chunks, then `audio.stop`
- **THEN** each of the 52 WS messages SHALL be a serialized `Frame`
- **AND** the 50 audio chunks SHALL have `payload == audio_data`
- **AND** the plugin SHALL accumulate the PCM bytes from each `AudioData.pcm` field into the capture buffer

#### Scenario: A binary frame arrives outside audio capture
- **WHEN** the plugin receives an `AudioData` frame while not in capturing state
- **THEN** the plugin SHALL log a debug message and silently drop the frame
- **AND** SHALL NOT crash

### Requirement: Codegen pipeline generates Python and TypeScript stubs

The plugin SHALL include a `buf.gen.yaml` (or equivalent `protoc` configuration) that produces:
- Python stubs at `plugin/src/byoa_plugin/proto_gen/` (typed, with `py.typed` marker)
- TypeScript stubs at `glasses-app/src/proto_gen/` (using `ts-proto` for clean idiomatic output)

The generated directories SHALL be committed to git (no runtime codegen). A `make proto` target (or equivalent npm script) SHALL regenerate both directories from the `.proto` file. CI SHALL verify generated stubs are not stale.

#### Scenario: Contributor regenerates stubs
- **WHEN** a contributor runs `make proto` after editing `hermes_bridge.proto`
- **THEN** both `plugin/src/byoa_plugin/proto_gen/` and `glasses-app/src/proto_gen/` SHALL be updated atomically
- **AND** the contributor can commit the regenerated files alongside the `.proto` change

#### Scenario: Generated stubs are stale in CI
- **WHEN** CI runs `make proto && git diff --exit-code` and the working tree has changes
- **THEN** CI SHALL fail with a message indicating the stubs need regeneration

### Requirement: wire.py and wire.ts wrappers preserve constructor call-site names

The plugin SHALL ship `plugin/src/byoa_plugin/wire.py` and `glasses-app/src/wire.ts` that re-export frame constructor functions with the SAME names as today's `protocol.py` (`hello`, `text`, `assistant_delta`, `tool_start`, etc.). The constructors SHALL return `bytes` (Python) / `Uint8Array` (TypeScript) instead of `str`. Existing call sites in `server.py`, `adapter.py`, `hooks.py`, `main.ts` SHALL need only an import path change.

#### Scenario: Existing call site continues to work
- **WHEN** the plugin code calls `wire.assistant_delta("hello")`
- **THEN** the return value SHALL be a serialized `Frame` as bytes
- **AND** the call site SHALL NOT need to construct a `Frame(payload=AssistantDeltaFrame(text="hello"))` manually

### Requirement: No transport change

The plugin SHALL continue to use the `websockets` Python library for the WS server and the glasses-app SHALL continue to use the browser-native `WebSocket` API. No proxy layer SHALL be added (no Envoy, no nginx-in-front-for-grpc-web). The TLS termination story (Tailscale Serve or reverse proxy) SHALL remain unchanged.

#### Scenario: Tailscale Serve deployment
- **WHEN** the plugin runs under Tailscale Serve (per existing deployment patterns)
- **THEN** the plugin SHALL continue to bind to loopback and SHALL continue to accept WS connections proxied by Tailscale
- **AND** no additional infrastructure SHALL be required
