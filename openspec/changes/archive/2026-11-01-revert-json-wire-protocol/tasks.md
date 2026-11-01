## 1. Restore JSON protocol module (plugin)

- [x] 1.1 Restore `plugin/src/byoa_plugin/protocol.py` from git 8437113^ with JSON frame constructors, `parse_client`, `STREAMING_CURSOR`, exception classes, and `INBOUND_TYPES`/`OUTBOUND_TYPES` constants. Update module docstring to note TS side is hand-maintained.
- [x] 1.2 Restore `plugin/tests/test_protocol.py` from git 8437113^ with all characterization tests (constructor shapes, parser error paths, constant counts).
- [x] 1.3 Run `uv run pytest tests/test_protocol.py -q` — all tests pass green.

## 2. Adapt plugin call sites

- [x] 2.1 Update `plugin/src/byoa_plugin/server.py`: change import from `wire` to `protocol`; change first-frame expectation from `WSMsgType.BINARY` to `WSMsgType.TEXT`; parse via `parse_client` + `t` dispatch instead of `parse_frame` + `WhichOneof`; remove `audio_data` case, add BINARY → raw PCM accumulation in capture state; change `_send_frame` from `ws.send_bytes(data)` to `ws.send_str(data)`.
- [x] 2.2 Update `plugin/src/byoa_plugin/connections.py`: change `send_frame(chat_id, frame)` parameter type from `bytes` to `str`; change `ws.send_bytes(frame)` to `ws.send_str(frame)`; update import from `wire` to `protocol`.
- [x] 2.3 Update `plugin/src/byoa_plugin/adapter.py`: change import from `wire as proto` to `protocol as proto`; verify all constructor calls work with `str` return values (signatures unchanged).
- [x] 2.4 Update `plugin/src/byoa_plugin/hooks.py`: change import from `wire as proto` to `protocol as proto`; verify `tool_start`, `tool_end`, `active`, `error`, `transcript` constructor calls work.
- [x] 2.5 Update `plugin/src/byoa_plugin/debug_client.py`: rewrite for JSON text frames — `parse_send_spec` returns `str` instead of `bytes`, `ws.send()` takes `str`, inbound parsing uses `json.loads` + `t` field instead of `parse_frame` + `WhichOneof`, drop `google.protobuf.json_format` import.
- [x] 2.6 Update `plugin/tests/fake_client.py`: rewrite for JSON text frames — all `send_*` methods call `ws.send(str)`, `recv_one`/`drain` parse JSON text frames, `send_binary(data)` sends raw bytes (not `wire.audio_data` wrapped), drop `hermes_bridge_pb2` import.
- [x] 2.7 Port `plugin/tests/test_debug_client.py`: replace `from byoa_plugin import wire` with `from byoa_plugin import protocol`; replace every `wire.parse_frame(serialized)` + `frame.WhichOneof("payload")` assertion with `protocol.parse_client(serialized)` + `frame["t"]`; replace protobuf field access with JSON dict keys per the restored constructors (`frame.text.content` → `frame["text"]`, `frame.sessions_switch.target` → `frame["id"]`, oneof names `sessions_list`/`sessions_new`/`audio_start`/`audio_stop`/... → dotted `t` values `sessions.list`/`sessions.new`/`audio.start`/`audio.stop`/...). `parse_send_spec` label assertions stay unchanged.
- [x] 2.8 Update `plugin/tests/test_connections.py`: rename the FakeWS `send_bytes` method to `send_str` (the registry's `send_frame` now calls `ws.send_str`); the `sent: list[str]` type and all identity-guard assertions are unchanged.
- [x] 2.9 Run `uv run ruff check src/ tests/` — clean. Run `uv run basedpyright src/` — clean. Run `uv run pytest -q` — all tests pass.

## 3. Restore JSON protocol types and adapt glasses-app

- [x] 3.1 Restore `glasses-app/src/protocol.ts` from git 734bd69^ with JSON frame type definitions, `parseOutbound`, `send*` helpers, and `STREAMING_CURSOR` export.
- [x] 3.2 Update `glasses-app/src/main.ts`: replace `from './wire'` imports with `from './protocol'`; replace `parseFrame(Uint8Array)` with `JSON.parse` + type narrowing; replace `ws.send(Uint8Array)` with `ws.send(JSON.stringify(...))` for control frames; replace `handleFrame(Frame)` with `handleFrame(Record<string, unknown>)`; replace `wireAudioStart`/`wireAudioStop`/`wireText`/etc. with `sendAudioStart`/`sendAudioStop`/`sendText`/etc. from protocol.ts; remove `audioData`/`wireAudioData` (PCM goes as raw binary via `ws.send(pcm.buffer)`); change `socket.binaryType` and `onmessage` to handle TEXT messages (JSON) and BINARY messages (raw PCM). (`frames.ts` does not exist at HEAD — it was deleted as dead code; the local `handleFrame` in main.ts is the sole dispatch site and is retyped here.)
- [x] 3.3 Port `glasses-app/tests/delta-accumulation.test.ts`: replace `import { parseFrame, assistantDelta, assistantFull, turnDone } from '../src/wire'` with imports from `../src/protocol` (`parseOutbound` plus the `AssistantDeltaFrame`/`AssistantFullFrame`/`TurnDoneFrame` types); replace the protobuf constructor calls (`assistantDelta('Hello ')`, `assistantFull(...)`, `turnDone()`) with JSON frame objects (`{ t: 'assistant.delta', text: 'Hello ' }`, `{ t: 'assistant', text: ... }`, `{ t: 'turn.done' }`) round-tripped through `parseOutbound`; replace `.assistantDelta?.text` / `.assistant?.text` / `.turnDone` field access with the JSON frame's `text` field narrowed on `t`. The accumulation assertions (delta appends, full replaces, turn.done clears) are unchanged.
- [x] 3.4 Run `npm run typecheck && npm run lint && npm run test` — all pass.

## 4. Delete Protobuf infrastructure

- [x] 4.1 Delete `plugin/proto/` (hermes_bridge.proto).
- [x] 4.2 Delete `plugin/buf.yaml` and `plugin/buf.gen.yaml`.
- [x] 4.3 Delete `plugin/src/byoa_plugin/proto_gen/` (generated stubs).
- [x] 4.4 Delete `plugin/src/byoa_plugin/wire.py`.
- [x] 4.5 Delete `plugin/tests/test_wire.py`.
- [x] 4.6 Delete `glasses-app/src/wire.ts`.
- [x] 4.7 Delete `glasses-app/src/proto_gen/`.
- [x] 4.8 Delete `glasses-app/tests/wire.test.ts`.

## 5. Strip Protobuf dependencies

- [x] 5.1 Edit `plugin/pyproject.toml`: remove `protobuf>=6.33.5,<7` from `[project.dependencies]`; remove `[dependency-groups]` `dev` entry containing `mypy-protobuf`; remove the inline comment about protobuf version compatibility; remove the `[tool.ruff]` `exclude = ["src/byoa_plugin/proto_gen"]` line (the dir is deleted by task 4.3, so the exclude is dead).
- [x] 5.2 Run `cd plugin && uv sync` — lockfile updated, `protobuf` and `mypy-protobuf` removed from `.venv`.
- [x] 5.3 Edit `glasses-app/package.json`: remove `@bufbuild/protobuf` from `dependencies`; remove `@bufbuild/buf` and `ts-proto` from `devDependencies`; remove `allowScripts.@bufbuild/buf` entry.
- [x] 5.4 Run `cd glasses-app && npm install` — lockfile updated, buf/protobuf deps removed.

## 6. Remove proto codegen from build system and CI

- [x] 6.1 Edit `Taskfile.yml`: remove `proto` and `proto-check` tasks; update `check` task to NOT include `proto-check`.
- [x] 6.2 Edit `.github/workflows/ci.yml`: remove the `proto-check` job entirely; remove `buf`/`ts-proto` setup from any remaining job (the `glasses-app` job's `npm ci` will no longer pull them after dep removal).
- [x] 6.3 Verify `task check` runs successfully (plugin-check + app-check only, no proto check). *(Landing env lacks the `task` binary; the equivalent component gates were run directly — plugin: `uv run pytest -q` / `ruff check src/ tests/` / `basedpyright src/`, all green; app: `npm run test` / `typecheck` / `lint` / `build`, all green.)*

## 7. Update documentation

- [x] 7.1 Edit `AGENTS.md`: delete the "Protocol codegen" section; replace with a "Wire protocol" section documenting the JSON frame format, `t` discriminator, raw binary PCM, and the dual-source-of-truth (protocol.py + protocol.ts hand-synced).
- [x] 7.2 Verify `README.md` needs no wire change: the architecture diagram already reads "hello + token" / "assistant.delta / tool.start/end" / "binary PCM frames" (README:13) with no protobuf references, and "binary PCM frames" stays true post-revert (raw PCM remains a BINARY WS frame). Edit only if a stale reference is actually found.
- [x] 7.3 Edit `DEVELOPER_NOTES.md`: if it references protobuf or buf, update.
- [x] 7.4 Add a **Rollout note** section to `design.md` (or a separate `ROLLOUT.md` in the change dir): deploy = re-sync `/opt/data/plugins/even-g2` + `plugins enable` + delayed gateway restart (landing lane owns all); glasses-app must be rebuilt (`npm run build`) and re-sideloaded to the phone by Doni — this is a manual step.

## 8. Validate and gate

- [x] 8.1 Run `openspec validate revert-json-wire-protocol --strict` — must pass with zero warnings. *(Ran via `npx @fission-ai/openspec` — CLI not installed on PATH in the landing env; result: valid.)*
- [x] 8.2 Run `uv run ruff check src/ tests/` in plugin/ — clean.
- [x] 8.3 Run `uv run basedpyright src/` in plugin/ — clean.
- [x] 8.4 Run `uv run pytest -q` in plugin/ — all tests pass.
- [x] 8.5 Run `npm run typecheck && npm run lint && npm run test` in glasses-app/ — all pass.
- [x] 8.6 Run `task check` at repo root — all CI gates pass. *(Same note as 6.3: component gates run directly, all green.)*
- [x] 8.7 Run `openspec validate revert-json-wire-protocol --strict` one final time before landing. *(Ran via `npx @fission-ai/openspec` after task ticks — result: valid.)*
