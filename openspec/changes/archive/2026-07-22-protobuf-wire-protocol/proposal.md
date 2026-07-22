## Why

The bridge ↔ glasses-app connection is currently broken and **undiagnosable**. The glasses-app repeatedly connects then immediately disconnects, and there is no logging on either side that would tell us why. The hand-rolled JSON-over-WebSocket protocol is the root cause of both problems:

1. **No schema discipline.** Frame shapes live in two parallel files (`plugin/src/byoa_plugin/protocol.py` and `glasses-app/src/protocol.ts`) kept in sync by a codegen script (`protocol_gen.py`) that emits TypeScript types from Python dict literals. Any field rename, any typo, any missing optional silently produces a runtime failure on the other side. The `parse_client` function accepts any JSON object with a `t` field — no shape validation at all.
2. **No standard tooling.** Today's frames are JSON strings, so the only way to inspect traffic is `wscat` with hand-typed JSON. There is no Postman collection, no `grpcurl`-equivalent, no way to decode captured bytes other than reading them. The plugin has no debug CLI.
3. **No structured logging.** Connection lifecycle events (open, hello received, auth check, register, dispatch loop entry, close) are scattered across `server.py:_handle_connection` with inconsistent log levels. Frame-level traffic isn't logged at all. Auth failures log a one-line warning with no context. There's no way to answer "what did the glasses send, what did we reply, where did the loop break?"
4. **Binary audio frames share the WS with text frames** via a runtime `isinstance(raw, bytes)` check that depends on capturing state. Subtle race conditions between an `audio.stop` text frame arriving in the same tick as a stray PCM chunk can silently drop audio or misclassify the control frame.

The user has explicitly asked: stop hand-rolling the protocol, use codegen, use Protobufs (compact + schema-defined), make the connection debuggable with standard tooling.

## What Changes

**Replace the JSON-over-WebSocket protocol with Protobuf-over-WebSocket.** One `.proto` file becomes the single source of truth for every frame shape; Python and TypeScript stubs are generated from it via `buf generate` (or `protoc` directly). WS messages are now binary Protobuf bytes instead of JSON strings. The transport itself stays WebSocket — no Envoy proxy, no new ops surface.

- **Add** `plugin/proto/hermes_bridge.proto` defining every current frame (8 inbound + 11 outbound + an `AudioData` variant for binary PCM) as a single `Frame` message with a `oneof payload` discriminator. Audio chunks are wrapped in `AudioData` frames so every WS binary message is a `Frame` — eliminates the `isinstance(raw, bytes)` heuristic.
- **Add** `buf` to the plugin dev deps (`pyproject.toml`) and a `buf.gen.yaml` that configures Python (`mypy-grpc` or `protoc-gen-mypy`) + TypeScript (`ts-proto`) output paths. Generated stubs land in `plugin/src/byoa_plugin/proto_gen/` and `glasses-app/src/proto_gen/`. Both directories are committed (per existing `protocol.ts` convention) so consumers don't need `buf` installed.
- **Add** a `Makefile` target (`make proto`) that runs `buf generate` from a single command and fails the build if generated files are stale (CI catches drift).
- **Replace** `plugin/src/byoa_plugin/protocol.py` with `plugin/src/byoa_plugin/wire.py` — a thin module that imports the generated stubs and re-exports the same constructor names (`hello`, `text`, `assistant_delta`, etc.) so existing call sites in `server.py`, `adapter.py`, `hooks.py` need only an import change. The constructors now return `bytes` (serialized Protobuf) instead of `str` (JSON).
- **Replace** `plugin/src/byoa_plugin/protocol_gen.py` with the `buf` pipeline. Delete `protocol_gen.py`.
- **Replace** `glasses-app/src/protocol.ts` with `glasses-app/src/proto_gen/index.ts` (buf output) + a thin `glasses-app/src/wire.ts` wrapper that exports the same types callers use today.
- **Update** `plugin/src/byoa_plugin/server.py` to decode inbound WS messages as `Frame.ParseString`/`ParseFrom(bytes)` and encode outbound via the generated stubs. The `isinstance(raw, bytes)` audio heuristic disappears — every binary frame is a `Frame` with `payload == AudioData`.
- **Update** `glasses-app/src/main.ts` to use the new generated stubs for `sendFrame` and the inbound dispatcher. Type narrowing via the Protobuf oneof replaces the `frame.t as string` cast.
- **Add** structured logging on both sides. Every frame inbound and outbound is logged at INFO with fields: `direction`, `frame_type`, `byte_size`, `chat_id` (when known). Connection lifecycle events (open, hello, auth_check, register, dispatch_loop_enter, abnormal_close, normal_close) logged at INFO with reason fields. Auth failures include the reason code (`bad_token`, `missing_token`, `malformed_hello`) and the originating `chat_id`.
- **Add** `plugin/src/byoa_plugin/debug_client.py` — a CLI tool run via `uv run python -m byoa_plugin.debug_client --url ws://... --token ...`. It connects, sends `hello`, logs every inbound frame at DEBUG (full payload, decoded via the generated stubs), and lets the user send canned frames via flags (`--send text:hello`, `--send audio.start`, `--send sessions.list`). This is the "standard tooling" the user can use to reproduce the connect-then-disconnect bug.
- **Add** `plugin/tests/test_wire.py` replacing `test_protocol.py` — same coverage shape (every constructor produces a parseable frame, every frame type round-trips, malformed input raises typed errors) but exercising the generated stubs.
- **Update** `plugin/tests/test_integration_ws.py` and `glasses-app/tests/*.test.ts` to use the new wire format. The fake client (`plugin/tests/fake_client.py`) gains a `send_frame(Frame)` helper that takes a typed Frame instead of hand-built JSON.
- **Migrate** the `align-glasses-app-best-practices` and `fix-session-rendering` main specs' references from "JSON frame" to "Frame" where the spec text describes wire format. (Out of scope: changing the spec semantics — only the encoding terminology changes.)
- **No change** to the WS transport (still `websockets` library), TLS (still Tailscale/reverse-proxy), or the auth handshake semantics (still hello + token). Only the encoding changes.

## Capabilities

### New Capabilities
- `protobuf-wire-protocol`: Defines the `.proto` schema as the single source of truth, the codegen pipeline (`buf generate` produces Python + TypeScript stubs), the binary Frame wire format, and the migration of every current frame type. Covers the `Frame` oneof envelope, `AudioData` wrapping, and the constructor module shape that keeps existing call sites diffable.
- `connection-debugging`: Defines the structured logging contract (frame-level INFO with fields, lifecycle events, auth failure reasons), the debug CLI client (`uv run python -m byoa_plugin.debug_client`), and the "no silent failures" rule (every decode error, every connection close, every malformed frame is logged with enough context to reproduce).

### Modified Capabilities
- `plugin-session-hooks`: Frame names change from string discriminators (`"active"`, `"sessions"`) to Protobuf oneof variants (`Frame.active`, `Frame.sessions`). No semantic change to the hook handlers themselves.

## Impact

- **Code**: ~250 lines new in `plugin/proto/hermes_bridge.proto` (the schema), ~80 lines in `plugin/src/byoa_plugin/wire.py` (thin re-export wrapper), ~80 lines in `glasses-app/src/wire.ts` (same), ~150 lines new in `plugin/src/byoa_plugin/debug_client.py`, ~200 lines of structured logging additions across `server.py`, `adapter.py`, `main.ts`. Deletions: `protocol.py` (221 lines), `protocol_gen.py` (211 lines), `glasses-app/src/protocol.ts` (regenerated file). Net delta roughly +200 lines after deletions.
- **Dependencies**: New build-time only — `buf` (via `uv` tool install), `ts-proto` (npm dev dep), `protoc-gen-mypy` or equivalent (uv dev dep). Zero new runtime dependencies on either side; Protobuf runtime (`protobuf` Python package, `protobufjs` npm package) is already transitively present or trivially small.
- **Behavior**: User-visible — the connect-then-disconnect bug becomes diagnosable (the new logging will surface the failure path on the first reproduction). The wire format change is invisible to end users; both sides upgrade together (no third-party clients to coordinate).
- **Testing**: All existing tests must pass after migration. New tests: `plugin/tests/test_wire.py` (~15 tests covering every constructor + parser round-trip), `plugin/tests/test_debug_client.py` (~5 tests for the CLI's canned-frame send + frame log), `glasses-app/tests/wire.test.ts` (~10 tests). The fake WS client gains typed Frame helpers.
- **Rollback**: Not clean — once both sides speak Protobuf, reverting requires reverting both sides simultaneously. Mitigation: ship as one PR with full CI gate; keep `protocol.py` and `protocol_gen.py` deleted in the same commit so there's no temptation to "temporarily" fall back.
- **Non-goals**: Switching transport (still WebSocket — gRPC was rejected for browser-WebView constraint, Socket.IO for not solving the schema problem). Adding new frame types (this change is purely a schema/encoding migration; new frames come in follow-ups). Changing the auth handshake semantics. Multi-version wire-format support (both sides always upgrade together). Touching `bridge-server/` (separate BYOA path).
