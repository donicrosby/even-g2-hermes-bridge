## 1. Proto schema (single source of truth)

- [ ] 1.1 Create `plugin/proto/hermes_bridge.proto`. Add `syntax = "proto3";` and `package hermes_bridge.v1;`.
- [ ] 1.2 Define 19 frame messages matching today's `protocol.py` constructors (8 inbound + 11 outbound). Use field names that match today's JSON keys (`token`, `device`, `text`, `id`, `name`, `active`, `caps`, `items`, `msg`, `ok`, `label`, `emoji`, `completed`, `interrupted`, `model`, `platform`). Use `optional` on fields that are absent in today's JSON when not set (e.g., `HelloOkFrame.active`, `ActiveFrame.name`, `ToolStartFrame.label`).
- [ ] 1.3 Define `AudioDataFrame { bytes pcm = 1; }` for the new binary PCM variant.
- [ ] 1.4 Define the top-level `Frame` message with `oneof payload` covering all 20 variants (19 control + 1 audio_data). Use field numbers 1-8 for inbound, 11-21 for outbound, 99 for `audio_data` (high number leaves room for future control frames without renumbering).
- [ ] 1.5 Add a file-level comment linking to the original `protocol.py` for historical context, and a `// KEEP FIELD NUMBERS STABLE` warning (renaming a field is safe; renumbering breaks wire compatibility).
- [ ] 1.6 Define a Python enum or constant for `chat_id` magic values if needed (none today, but document that `chat_id` is NOT a field in any frame — it's a connection-level concept tracked by the server).

## 2. Codegen pipeline

- [ ] 2.1 Add `plugin/buf.yaml` configuring the `buf` module (path: `proto`, lint: DEFAULT, breaking: FILE).
- [ ] 2.2 Add `plugin/buf.gen.yaml` configuring two plugins:
  - Python: `protoc-gen-mypy` (or `buf.build/community/protocolbuffers-python-vitalik)`, output: `src/byoa_plugin/proto_gen/`.
  - TypeScript: `ts-proto` (via `npm install --save-dev ts-proto`), output: `../../glasses-app/src/proto_gen/`.
- [ ] 2.3 Add a root `Taskfile.yml` (per user preference — not Makefile) with a `proto` task: `dir: plugin; cmds: [buf generate]`. Also add a `proto-check` task that runs `task proto` then `git diff --exit-code` on the generated dirs. Document the install at https://taskfile.dev/installation/ in `plugin/README.md`.
- [ ] 2.4 Add `@bufbuild/buf` (npm wrapper around the buf Go binary — no separate tool install needed) and `ts-proto` to `glasses-app/devDependencies` via `npm install --save-dev`. Verify `npx buf --version` works.
- [ ] 2.5 Add `protobuf` as a runtime dep and `mypy-protobuf` (or `protoc-gen-mypy`) as a dev dep in `plugin/pyproject.toml` via `uv add protobuf && uv add --dev mypy-protobuf`. Verify `uv run python -c "import google.protobuf"` works.
- [ ] 2.6 Add `glasses-app/src/proto_gen/` and `plugin/src/byoa_plugin/proto_gen/` to `.gitignore` EXCLUSIONS (so they ARE committed despite parent gitignore rules). Add headers to each generated file warning they're generated.
- [ ] 2.7 Run `task proto` and verify both output directories contain the expected files (`hermes_bridge_pb2.py` / `index.ts` etc.). Commit the generated files.

## 3. wire.py / wire.ts thin wrappers

- [ ] 3.1 Create `plugin/src/byoa_plugin/wire.py`. Import the generated `Frame`, `HelloFrame`, etc. from `.proto_gen.hermes_bridge_pb2`. Re-export constructor functions named exactly like today's `protocol.py`: `hello(token, device)`, `text(content)`, `audio_start()`, `audio_stop()`, `sessions_list()`, `sessions_switch(target)`, `sessions_new()`, `stop()`, `hello_ok(active=None, caps=None)`, `assistant_delta(text)`, `assistant_full(text)`, `tool_start(name, label=None, emoji=None)`, `tool_end(name, ok=True)`, `turn_done()`, `sessions(items, active=None)`, `active(session_id, name=None)`, `history(session_id, items, ok=True)`, `transcript(text)`, `error(message)`. Each returns `bytes` (serialized `Frame`).
- [ ] 3.2 Add `parse_frame(raw: bytes) -> Frame` to `wire.py` that wraps the generated `Frame.FromString(raw)`. On `DecodeError` raise a typed `FrameParseError` (re-export from `wire`).
- [ ] 3.3 Create `glasses-app/src/wire.ts`. Import from `./proto_gen`. Re-export the same constructor names returning `Uint8Array`. Add `parseFrame(raw: Uint8Array): Frame` wrapper.
- [ ] 3.4 Verify both wrappers typecheck and round-trip with a quick smoke test in their respective test suites.

## 4. Server migration (plugin/src/byoa_plugin/server.py)

- [ ] 4.1 Replace `from byoa_plugin import protocol as proto` with `from byoa_plugin import wire` in `server.py`. No constructor renames needed (wrapper preserves names).
- [ ] 4.2 Update `_handle_connection` Phase 1 (hello handshake) to:
  - Receive first frame as bytes (WS binary).
  - `try: frame = wire.parse_frame(raw); except FrameParseError: log + close 1002`.
  - Switch on `frame.WhichOneof("payload")`. If not `"hello"`, close 1002 with `wrong_first_frame` reason.
  - Read `frame.hello.token`, `frame.hello.device`. Constant-time compare token via `hmac.compare_digest` (preserved from today).
- [ ] 4.3 Update Phase 2 (dispatch loop):
  - `async for raw in ws:` — `raw` is always bytes now (binary WS).
  - `try: frame = wire.parse_frame(raw)`.
  - Switch on `frame.WhichOneof("payload")`:
    - `"audio_data"` → if `capturing`, extend `audio_buf` with `frame.audio_data.pcm`; else debug-log and drop.
    - `"text"` → call `_on_text(chat_id, frame.text.content)`.
    - `"audio_start"` → set `capturing = True`, clear buffer.
    - `"audio_stop"` → set `capturing = False`, dispatch `_on_audio_stop(chat_id, bytes(audio_buf))`, clear buffer.
    - `"sessions_list"`, `"sessions_switch"`, `"sessions_new"`, `"stop"` → call respective handlers.
    - `None` (empty frame, no payload set) → warn + continue.
- [ ] 4.4 Remove the `isinstance(raw, (bytes, bytearray, memoryview))` branch entirely — every binary message is now a `Frame`.
- [ ] 4.5 Remove the `proto.parse_client(raw)` text-mode call path.

## 5. Adapter, hooks, http_endpoints migration

- [ ] 5.1 Update `adapter.py` imports from `protocol as proto` to `wire`. All call sites unchanged (constructor names preserved).
- [ ] 5.2 Update `hooks.py` imports similarly. Verify `_pre_tool_call` and `_on_session_*` handlers still emit frames correctly.
- [ ] 5.3 Update `http_endpoints.py` if it references any frame constructors.
- [ ] 5.4 Sanity grep: `grep -rn "from byoa_plugin import protocol" plugin/src/` should return zero hits.

## 6. Glasses-app migration (glasses-app/src/main.ts)

- [ ] 6.1 Replace imports from `./protocol` with `./wire`. The exported type names should be unchanged (the wrapper re-exports them); if not, add type re-exports to `wire.ts`.
- [ ] 6.2 Update `sendFrame` to accept a `Uint8Array` (or a `Frame` it serializes itself) and call `ws.send(uint8)` instead of `ws.send(JSON.stringify(...))`.
- [ ] 6.3 Update `handleFrame` dispatcher to consume a decoded `Frame` instead of a `Record<string, unknown>`. Replace the `frame.t as string` cast with the Protobuf oneof discriminator.
- [ ] 6.4 Update each `handleX` function's signature to accept the typed sub-frame (e.g., `handleHelloOk(frame: HelloOkFrame)`).
- [ ] 6.5 Update the WS message handler: `ws.onmessage = (e) => { const bytes = new Uint8Array(e.data); const frame = parseFrame(bytes); handleFrame(frame); }`. (Today it parses JSON; now it parses Protobuf.)
- [ ] 6.6 Update audio PCM sending: the audio capture path now wraps each chunk in `wire.audio_data(pcm)` before sending.

## 7. Structured logging (Python side)

- [ ] 7.1 Add `structlog` to `plugin/pyproject.toml` runtime deps (`uv add structlog`).
- [ ] 7.2 Add `plugin/src/byoa_plugin/log.py` configuring `structlog` with JSON output, INFO default level, and an env-var override (`EVEN_G2_LOG_LEVEL=DEBUG`).
- [ ] 7.3 Replace stdlib `logging.getLogger` imports in `server.py`, `adapter.py`, `hooks.py`, `connections.py`, `http_endpoints.py` with `from byoa_plugin.log import get_logger; LOG = get_logger(__name__)`.
- [ ] 7.4 Add frame-level logging in `server.py:_handle_connection`:
  - Inbound: after `frame = wire.parse_frame(raw)`, `LOG.info("frame", direction="in", frame_type=frame.WhichOneof("payload"), byte_size=len(raw), chat_id=chat_id)`.
  - Outbound: wrap every `await ws.send(bytes)` in a helper `await send_frame(ws, chat_id, frame_bytes, frame_type)` that logs the same shape with `direction="out"`.
- [ ] 7.5 Add lifecycle logging in `_handle_connection`: `ws_open`, `hello_received`, `auth_check (result)`, `auth_failed (reason)`, `registered`, `dispatch_loop_enter`, `dispatch_loop_exit`, `normal_close (code, reason)`, `abnormal_close (code, reason, exception)`.
- [ ] 7.6 Add structured error logging in every `except` block. Replace `LOG.warning("auth rejected: bad token")` with `LOG.warning("auth_failed", chat_id=cid, reason="bad_token")`. Replace generic `except Exception as e: LOG.warning(...)` with structured `LOG.error("send_frame_error", chat_id=cid, frame_type=..., error=str(e), error_type=type(e).__name__)`.

## 8. Structured logging (TypeScript side)

- [ ] 8.1 Create `glasses-app/src/log.ts` with a tiny logger: `log.info("frame", { direction: "in", frame_type: "...", byte_size: 24 })` → `console.log(JSON.stringify({ level: "info", event: "frame", ...fields, ts: Date.now() }))`. (The Flutter WebView captures `console.log` for the host app's log surface.)
- [ ] 8.2 Add frame-level logging in `main.ts` around `sendFrame` and the `ws.onmessage` handler.
- [ ] 8.3 Add lifecycle logging: `ws_opening`, `ws_open`, `ws_close`, `ws_error`, `hello_sent`, `hello_ok_received`, `auth_failed`.

## 9. Debug CLI client

- [ ] 9.1 Create `plugin/src/byoa_plugin/debug_client.py`. Use `argparse` for flags: `--url`, `--token`, `--send` (multiple), `--debug` (verbose payload), `--timeout` (default 30s).
- [ ] 9.2 Connect via `websockets.connect(url)`. Send `wire.hello(token, "debug-client")`. Await first frame; assert it's `hello_ok`. Log `connected`.
- [ ] 9.3 Loop receiving frames, log each at INFO (type + size) and DEBUG (full decoded payload via `MessageToDict`).
- [ ] 9.4 Send each `--send` frame after hello. Parse the spec format `<frame_type>:<arg>` (e.g., `text:hello`, `sessions.list`, `audio.start`). Use the `wire.*` constructors.
- [ ] 9.5 On `KeyboardInterrupt` or timeout, emit a summary table of frames sent/received grouped by type, then exit 0.
- [ ] 9.6 Add tests at `plugin/tests/test_debug_client.py`: cover the spec parser (`text:hello` → `(wire.text, "hello")`), the summary formatter, and a smoke test against a fake WS server.

## 10. Test migration

- [ ] 10.1 Replace `plugin/tests/test_protocol.py` with `plugin/tests/test_wire.py`. Cover: every constructor produces a parseable `Frame`; every variant round-trips through `parse_frame`; `parse_frame` raises `FrameParseError` on malformed bytes; constructor names match today's exactly.
- [ ] 10.2 Update `plugin/tests/fake_client.py`: `send_frame(Frame)` helper that takes a typed `Frame` and serializes it. Replace any hand-built JSON in test fixtures.
- [ ] 10.3 Update `plugin/tests/test_integration_ws.py` to send/receive `Frame` objects via the new helpers.
- [ ] 10.4 Update `plugin/tests/test_session_hooks.py`, `test_hello_ok_active.py` for the new wire format.
- [ ] 10.5 Update `glasses-app/tests/*.test.ts` to use the new wire types. Add `glasses-app/tests/wire.test.ts` covering constructor round-trips.
- [ ] 10.6 Run `cd plugin && uv run pytest -q` — all tests pass.
- [ ] 10.7 Run `cd glasses-app && npm run test && npm run typecheck && npm run lint && npm run build` — all green.

## 11. Decommission old protocol files

- [ ] 11.1 Delete `plugin/src/byoa_plugin/protocol.py`.
- [ ] 11.2 Delete `plugin/src/byoa_plugin/protocol_gen.py`.
- [ ] 11.3 Delete `glasses-app/src/protocol.ts`.
- [ ] 11.4 Update `plugin/README.md` "Regenerating the TypeScript protocol module" section to describe `make proto` instead of `uv run python -m byoa_plugin.protocol_gen`.
- [ ] 11.5 Sanity grep: `grep -rn "from byoa_plugin import protocol" .` returns zero hits. `grep -rn "from .protocol" glasses-app/src/` returns zero hits.

## 12. Reproduce and fix the connect-then-disconnect bug

- [ ] 12.1 With structured logging in place, run the bridge locally and connect with the debug CLI. Capture the plugin logs.
- [ ] 12.2 With structured logging in place, run the glasses-app in the Even Hub simulator and connect to the local bridge. Capture both log streams.
- [ ] 12.3 Triangulate the failure: if the CLI succeeds but the glasses-app fails, the bug is in the glasses-app (likely the WS open path or the hello send). If both fail at the same step, the bug is in the plugin. The structured logs will show exactly which lifecycle event is missing or unexpected.
- [ ] 12.4 Fix the localized bug. Add a regression test that exercises the specific failure path.
- [ ] 12.5 Document the bug's root cause in a one-paragraph note in `plugin/README.md` "Troubleshooting" section so future contributors don't repeat the diagnosis.

## 13. CI gate

- [ ] 13.1 Add a CI job (or extend an existing one) that runs `task proto && git diff --exit-code -- plugin/src/byoa_plugin/proto_gen/ glasses-app/src/proto_gen/`. Fails on stale stubs.
- [ ] 13.2 Verify existing CI jobs (`uv run pytest`, `uv run ruff`, `uv run basedpyright`, `npm run test`, `npm run typecheck`, `npm run lint`, `npm run build`) all pass on the migrated codebase.

## 14. OpenSpec wrap

- [ ] 14.1 Run `openspec validate protobuf-wire-protocol` and fix any reported issues.
- [ ] 14.2 Stage and commit atomically per AGENTS.md convention. Suggested commit sequence:
  - `feat(plugin): add .proto schema + buf codegen pipeline` — sections 1, 2.
  - `feat(plugin,glasses-app): add wire.py/wire.ts wrappers around generated stubs` — section 3.
  - `refactor(plugin): migrate server, adapter, hooks to Protobuf wire format` — sections 4, 5.
  - `refactor(glasses-app): migrate main.ts to Protobuf wire format` — section 6.
  - `feat(plugin,glasses-app): structured frame-level + lifecycle logging` — sections 7, 8.
  - `feat(plugin): add debug_client CLI for connection troubleshooting` — section 9.
  - `test(plugin,glasses-app): migrate wire tests to Protobuf` — section 10.
  - `chore(plugin,glasses-app): delete legacy JSON protocol.py/protocol_gen.py/protocol.ts` — section 11.
  - `fix: reproduce and fix connect-then-disconnect bug` — section 12 (root cause TBD).
  - `docs(openspec): add protobuf-wire-protocol change` — openspec planning artifacts.
- [ ] 14.3 Each commit SHALL independently pass the full test suites. No commit SHALL leave the codebase in a half-migrated state.
