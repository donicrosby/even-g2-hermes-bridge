## Why

The WS wire protocol was migrated JSON→Protobuf on 2026-07-22. Protobuf has caused repeated production breakage:

1. **Unpinned buf remote plugin** drifted to v35.x gencode vs gateway runtime 6.33.5 → silent plugin load failure (fix 5c0ef9e).
2. **protoc/buf CI churn** across 4 follow-up commits (f1a1a34, d3041e1, 29386e1, 8536d3e) that patched buf setup, protoc installation, and gencode regeneration without adding user-visible value.
3. **Two dependency trees** (Python protobuf + TypeScript @bufbuild/protobuf) that must stay version-locked against the gateway image — any drift between the buf remote plugin, the gateway's protobuf runtime, and the generated stubs produces silent breakage with no diagnostic.

The JSON protocol worked for months before the migration. Its frame shapes are simple, the `t` discriminator is trivially verifiable by inspection, and zero external dependencies are required — `json.dumps`/`json.loads` are stdlib. The Hermes gateway container (v0.22.5) ships Python 3.14.9 + aiohttp; depending ONLY on those eliminates the entire dependency-drift surface.

Decision (Doni, 2026-11-01): revert the wire to plain JSON and depend only on what the Hermes gateway provides.

## What Changes

**Revert the plugin↔glasses-app WS wire from Protobuf binary to JSON text frames.** Restore the legacy `protocol.py` module (frame constructors, `parse_client` parser, `STREAMING_CURSOR`) and the legacy `protocol.ts` TypeScript types (discriminated union on `t`). Delete all Protobuf infrastructure: `.proto` schema, `buf` config, generated stubs, `wire.py`/`wire.ts` wrappers, CI proto-check job, Taskfile proto tasks.

- **Restore** `plugin/src/byoa_plugin/protocol.py` with the same frame families, constructor names, and signatures as the pre-migration version (git 8437113^). Constructors return `str` (JSON). Inbound types: hello, text, audio.start, audio.stop, sessions.list, sessions.switch, sessions.new, stop. Outbound types: hello.ok, assistant.delta, assistant, tool.start, tool.end, turn.done, sessions, active, history, transcript, error.
- **Restore** `glasses-app/src/protocol.ts` with JSON frame types (discriminated union on `t`), `parseOutbound`, and `send*` helper functions. Binary PCM remains raw binary WS frames between `audio.start`/`audio.stop` — no `AudioData` wrapping.
- **Adapt** `plugin/src/byoa_plugin/server.py` WS dispatch: TEXT frames (`WSMsgType.TEXT`) are JSON-parsed via `parse_client` and dispatched by `t` field; BINARY frames inside capture state are raw PCM (the `audio_data` protobuf variant goes away).
- **Adapt** `plugin/src/byoa_plugin/connections.py`: `send_frame` takes `str` (JSON) and calls `ws.send_str()` instead of `ws.send_bytes()`.
- **Adapt** call sites in `adapter.py`, `hooks.py`, `debug_client.py`, `fake_client.py`: import from `protocol` instead of `wire`, constructors return `str`, `send_frame` takes `str`.
- **Adapt** `glasses-app/src/main.ts`: import from `protocol` instead of `wire`, JSON `parseOutbound` instead of `parseFrame`, `ws.send(str)` instead of `ws.send(Uint8Array)`, retype the local `handleFrame` dispatch from `Frame` to `Record<string, unknown>`. Voice confirmation flow (listen→display→confirm→send) is entirely client-side and requires no wire changes.
- **Delete** all Protobuf infrastructure: `plugin/proto/`, `plugin/buf.yaml`, `plugin/buf.gen.yaml`, `plugin/src/byoa_plugin/proto_gen/`, `plugin/src/byoa_plugin/wire.py`, `plugin/tests/test_wire.py`, `glasses-app/src/wire.ts`, `glasses-app/src/proto_gen/`, `glasses-app/tests/wire.test.ts`, Taskfile `proto`/`proto-check` tasks, CI `proto-check` job + buf/protoc setup steps.
- **Strip deps**: `plugin/pyproject.toml` drops `protobuf` and the `mypy-protobuf` dev group. Final runtime deps: aiohttp, numpy, faster-whisper, qrcode, pillow, httpx, anyio. `glasses-app/package.json` drops `@bufbuild/protobuf`, `@bufbuild/buf`, `ts-proto`.
- **Update docs**: AGENTS.md protocol codegen section deleted/replaced. README.md, DEVELOPER_NOTES.md. `openspec/specs/glasses-ws-protocol` updated for JSON. `openspec/specs/protobuf-wire-protocol` archived.

BYOA HTTPS endpoint (`POST /v1/chat/completions`) is explicitly OUT of scope — untouched.

## Capabilities

### Modified Capabilities
- **glasses-ws-protocol**: Frame encoding changed from Protobuf binary to JSON text. Inbound parsing uses `json.loads` + `t` discriminator instead of `Frame.ParseFromString` + `WhichOneof`. Outbound constructors return `str` (JSON) instead of `bytes` (Protobuf). Binary audio PCM is raw WS binary frames, not `AudioData`-wrapped. All frame family names and field shapes preserved.
- **protobuf-wire-protocol**: All requirements removed. The single `.proto` source of truth, `oneof payload` envelope, `AudioData` wrapping, `buf` codegen pipeline, and `wire.py`/`wire.ts` wrapper modules are deleted. The `openspec/specs/protobuf-wire-protocol/` spec is archived.

## Impact

- **Code**: ~220 lines restored in `protocol.py` (faithful to git 8437113^), ~150 lines restored in `protocol.ts`, ~30 lines changed in `server.py` (BINARY→TEXT dispatch, `send_bytes`→`send_str`), ~5 lines changed in `connections.py` (`send_bytes`→`send_str`), ~5 lines changed in `adapter.py` (import path), ~10 lines changed in `hooks.py`, ~40 lines rewritten in `debug_client.py`, ~50 lines rewritten in `fake_client.py`, ~40 lines changed in `main.ts`. Deletions: `wire.py` (~170 lines), `wire.ts` (~180 lines), `test_wire.py` (~100 lines), `wire.test.ts` (~220 lines), `proto/` (~50 lines .proto + ~40 lines buf config), `proto_gen/` (~1000 lines generated), CI proto-check (~20 lines), Taskfile proto tasks (~15 lines). Net delta roughly -800 lines.
- **Dependencies**: Removed from plugin: `protobuf>=6.33.5,<7` (runtime), `mypy-protobuf` dev group. Removed from glasses-app: `@bufbuild/protobuf` (runtime), `@bufbuild/buf`, `ts-proto` (dev). Zero new dependencies on either side — `json` is stdlib.
- **Behavior**: No user-visible change to frame semantics — same frame families, same fields, same auth handshake, same streaming model. The `caps` list in `hello.ok` stays `["text", "voice", "tool-events", "sessions", "streaming"]`. Voice confirmation flow is client-side only and requires no wire changes (already uses `transcript` + `text` frames).
- **Rollback**: Clean — restoring a protobuf-era binary would require reverting both sides simultaneously, but neither side is deployed to production (plugin is disabled, glasses-app is sideloaded per-device).
- **Non-goals**: Touching BYOA HTTPS endpoint. Changing frame semantics (fields, auth, streaming model). Adding new frame types. Multi-version wire-format support. Touching bridge-server/ or probe/. Touching `/opt/data/plugins/even-g2` or live gateway config.