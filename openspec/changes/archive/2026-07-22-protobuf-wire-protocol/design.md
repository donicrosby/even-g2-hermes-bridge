## Context

Today's wire protocol is JSON-over-WebSocket. Frames are JSON strings with a `"t"` discriminator field (`"hello"`, `"assistant.delta"`, etc.). The schema lives in `plugin/src/byoa_plugin/protocol.py` (221 lines) as hand-written Python functions that return `json.dumps(...)` strings. A separate build-time script (`protocol_gen.py`, 211 lines) emits TypeScript types into `glasses-app/src/protocol.ts` so the client has matching types. Binary PCM audio frames are sent as raw WS binary messages with no envelope — the server uses `isinstance(raw, bytes)` plus a `capturing` boolean to distinguish them from text-mode control frames.

This setup has four pain points (detailed in `proposal.md`). The user has explicitly asked for codegen-driven schema (Protobuf) and standard debug tooling.

The glasses-app runs inside a Flutter WebView (per `AGENTS.md` and the Even Hub SDK reference). This rules out gRPC proper (browsers can't speak HTTP/2 trailers natively; would require an Envoy proxy in front of the plugin — operationally absurd for a Tailscale-served loopback service). Socket.IO was considered and rejected: it adds a transport layer without enforcing schema at the wire boundary, and Protobuf inspection at the Socket.IO layer is still manual.

**The chosen architecture: Protobuf over WebSocket.** Same transport, schema-defined binary encoding, standard tooling (`buf`, `protoc --decode`, generated stubs on both sides).

## Goals / Non-Goals

**Goals:**
- One `.proto` file is the single source of truth for every frame shape.
- Python and TypeScript stubs are generated, not hand-written.
- Every WS message is a typed `Frame` (no more `isinstance(raw, bytes)` heuristic).
- Every frame inbound/outbound is logged with structured fields.
- A debug CLI client can connect, send canned frames, and log every frame received.
- The connect-then-disconnect bug becomes reproducible from logs alone.

**Non-Goals:**
- New transport (still WebSocket).
- New frame types (pure migration; semantics preserved).
- Multi-version wire support (both sides always upgrade together — tightly coupled system).
- Touching `bridge-server/` (separate BYOA path).
- Backward-compat shim during migration (we ship a single atomic upgrade).

## Decisions

### D1: Single `Frame` message with `oneof payload` — not an envelope

**Choice.** Define one top-level `Frame` message with a `oneof payload` field covering all 19 current frame types + the new `AudioData` variant. Every WS message is exactly one serialized `Frame`. Receivers parse once and switch on the `payload` oneof discriminator.

```proto
message Frame {
  oneof payload {
    HelloFrame hello = 1;
    TextFrame text = 2;
    AudioStartFrame audio_start = 3;
    AudioStopFrame audio_stop = 4;
    SessionsListFrame sessions_list = 5;
    SessionsSwitchFrame sessions_switch = 6;
    SessionsNewFrame sessions_new = 7;
    StopFrame stop = 8;
    HelloOkFrame hello_ok = 11;
    AssistantDeltaFrame assistant_delta = 12;
    AssistantFullFrame assistant = 13;
    ToolStartFrame tool_start = 14;
    ToolEndFrame tool_end = 15;
    TurnDoneFrame turn_done = 16;
    SessionsFrame sessions = 17;
    ActiveFrame active = 18;
    HistoryFrame history = 19;
    TranscriptFrame transcript = 20;
    ErrorFrame error = 21;
    AudioDataFrame audio_data = 99;  // high field number — leaves room for new control frames
  }
}
```

**Rationale.** This is the canonical Protobuf pattern for variant types. Single parse, type-safe dispatch in both Python (`frame.WhichOneof("payload")`) and TypeScript (`frame.payload.$case`). Avoids the double-decode of an envelope-with-bytes-payload pattern. New frame types in the future just add a new oneof variant — non-breaking.

**Alternatives considered.**
- **Envelope with `bytes payload`** (`message Frame { FrameType type = 1; bytes payload = 2; }`). Rejected: double decode on every frame; loses type safety at the envelope boundary; requires manual dispatch instead of language-native switch.
- **Separate messages per direction** (`ClientFrame` and `ServerFrame`). Rejected: overlaps with `oneof` and creates two parallel type hierarchies. The `oneof` already gives us direction-agnostic framing; direction is enforced at the dispatch layer (server rejects client-only frames and vice versa).

### D2: Audio chunks wrapped in `AudioData` Frame variant — eliminates `isinstance(raw, bytes)` heuristic

**Choice.** PCM audio is wrapped in an `AudioDataFrame { bytes pcm = 1; }` variant of the `Frame` oneof. Every WS binary message is now a `Frame`. The server's audio-capture loop becomes:

```python
frame = Frame.FromString(raw_bytes)
kind = frame.WhichOneof("payload")
if kind == "audio_data":
    audio_buf.extend(frame.audio_data.pcm)
elif kind == "audio_stop":
    # stop capturing, dispatch
else:
    # normal control frame dispatch
```

**Rationale.** Today's `isinstance(raw, bytes)` check is fragile — it relies on the `capturing` flag being correctly maintained across async tasks. A stray binary frame during a state transition can be misclassified. Wrapping PCM in `AudioData` makes every message self-describing. The cost is ~3 bytes per audio chunk of overhead (Protobuf tag + length prefix); at 640-byte PCM chunks (20ms @ 16kHz mono), that's 0.5% overhead — negligible.

**Alternative considered.** Keep raw PCM as WS binary messages, use a magic-byte prefix to distinguish. Rejected: reinvents what Protobuf already gives us, defeats the "every message is a Frame" invariant.

### D3: Wire format is binary Protobuf, not JSON-serialized Protobuf

**Choice.** WS messages are binary (`opcode = 2`). Frame encoding is `frame.SerializeToString()` (Python) / `Frame.encode(frame).finish()` (ts-proto). No JSON variant.

**Rationale.** The user explicitly cited frame size as a concern ("we need the frame to be small and compact"). Binary Protobuf is ~30-60% smaller than JSON for our frame shapes (verified against `assistant.delta` and `sessions` payloads). It's also the canonical Protobuf encoding — JSON-serialized Protobuf (via `google.protobuf.json_format`) is for debugging only.

**Tradeoff.** Binary frames are harder to read in `wscat` — but that's exactly what the debug CLI client solves by decoding via the generated stubs.

### D4: `buf` for codegen, not raw `protoc` invocations

**Choice.** Use [buf](https://buf.build) (`buf generate`) for codegen. Config in `plugin/buf.gen.yaml` and `plugin/buf.yaml`. Generated stubs land in:
- `plugin/src/byoa_plugin/proto_gen/` (Python, via `protoc-gen-mypy` for types)
- `glasses-app/src/proto_gen/` (TypeScript, via `ts-proto`)

Both directories are git-committed (per the existing `protocol.ts` convention). CI verifies `buf generate` produces no diff (catches schema drift).

**Rationale.** `buf` is the modern standard for Protobuf tooling — single config file, multi-language output, plugin ecosystem, linting (`buf lint`), breaking-change detection (`buf breaking`). Raw `protoc` invocations are workable but require shell scripts that drift across environments.

**Alternatives considered.**
- **Raw `protoc` with Makefile rules.** Rejected: works but reinvents `buf`'s config-as-data model.
- **`grpc-tools` npm package.** Rejected: TypeScript-focused, doesn't handle the Python side.

### D5: `wire.py` / `wire.ts` thin wrappers preserve existing call-site names

**Choice.** Introduce `plugin/src/byoa_plugin/wire.py` and `glasses-app/src/wire.ts` as thin modules that import the generated stubs and re-export constructor functions with the SAME names as today (`hello(token, device)`, `assistant_delta(text)`, `tool_start(name, label=...)`, etc.). Constructors now return `bytes` (Python) / `Uint8Array` (TypeScript) instead of `str`.

Existing call sites in `server.py`, `adapter.py`, `hooks.py`, `main.ts` need only an import change (`from byoa_plugin import wire` instead of `from byoa_plugin import protocol as proto`).

**Rationale.** The migration touches ~30 call sites. Renaming every constructor (`proto.hello` → `Frame(payload=HelloFrame(...))`) would force 30 diffs in the same PR as the wire migration, making review harder. The wrapper keeps the constructor surface stable; the diffs stay focused on the encoding change.

**Alternative considered.** Skip the wrapper, use the generated stubs directly. Rejected for the review-complexity reason above. A follow-up change can remove the wrapper once the migration lands.

### D6: Structured logging — `structlog` on Python, tiny logger on TypeScript

**Choice.** Python: add `structlog` (already commonly used in Hermes-adjacent code) for structured key-value logs. TypeScript: tiny `src/log.ts` wrapper over `console.log` that emits JSON-shaped objects (consumed by the Flutter WebView's `console.log` → host log bridge).

Every frame inbound and outbound is logged at INFO with:
```python
LOG.info("frame", direction="in"|"out", frame_type=kind, byte_size=len(raw), chat_id=cid)
```

Connection lifecycle events logged at INFO:
```python
LOG.info("ws_open", chat_id=cid)
LOG.info("hello_received", chat_id=cid, has_token=True)
LOG.warning("auth_failed", chat_id=cid, reason="bad_token")
LOG.info("registered", chat_id=cid)
LOG.info("dispatch_loop_enter", chat_id=cid)
LOG.warning("abnormal_close", chat_id=cid, code=1008, reason="unauthorized")
LOG.info("normal_close", chat_id=cid)
```

Auth failures include the specific reason: `bad_token`, `missing_token`, `malformed_hello`, `wrong_first_frame`. Today's `LOG.warning("auth rejected: bad token")` is too generic to debug from.

**Rationale.** Structured logs are machine-parseable (filter by `chat_id`, group by `frame_type`, count `auth_failed` events). They're also human-readable. The current free-text logging makes grep painful and aggregation impossible.

**Alternative considered.** Use stdlib `logging` with `extra={...}`. Rejected: `structlog` gives much cleaner output formatting and is the de facto Python standard for structured logs.

### D7: Debug CLI client — `uv run python -m byoa_plugin.debug_client`

**Choice.** Add `plugin/src/byoa_plugin/debug_client.py` exposing a CLI:

```bash
# Connect, send hello, log every frame received for 30 seconds
uv run python -m byoa_plugin.debug_client --url ws://127.0.0.1:8767 --token $TOKEN

# Send a specific frame then watch
uv run python -m byoa_plugin.debug_client --url ... --token ... --send text:"hello world"
uv run python -m byoa_plugin.debug_client --url ... --token ... --send sessions.list
uv run python -m byoa_plugin.debug_client --url ... --token ... --send audio.start --then audio.stop

# Increase verbosity (default INFO; --debug shows full payload)
uv run python -m byoa_plugin.debug_client --url ... --token ... --debug
```

The CLI uses the generated stubs to construct frames (no second wire implementation). Logs every inbound frame at INFO (frame type + size), DEBUG (full decoded payload). Exits cleanly on Ctrl-C with a final summary (frames sent, frames received, by type).

**Rationale.** This is the "standard tooling" the user can use to reproduce the connect-then-disconnect bug. Today they have nothing. With this CLI plus the structured logging, the bug goes from "I have ZERO way to debug" to "run the CLI, attach logs, done."

## Risks / Trade-offs

- **[One-shot incompatible upgrade]** The wire format changes from JSON to Protobuf. An old plugin + new glasses-app (or vice versa) won't connect. → *Mitigation*: ship as one atomic PR; both sides upgrade together. There are no third-party clients to coordinate. CI gates both packages in the same repo.
- **[Generated stubs add ~2k LoC to the repo]** Both `plugin/src/byoa_plugin/proto_gen/` and `glasses-app/src/proto_gen/` will be large. → *Mitigation*: per the existing convention (`protocol.ts` is already committed), this is the established pattern. Generated files have a header warning and are excluded from ruff/eslint.
- **[buf is a new build-time dep]** Developers need `buf` installed to regenerate stubs. → *Mitigation*: `uv tool install buf` (or `npm install -g @bufbuild/buf`); one-time setup. Documented in `plugin/README.md`. CI catches stale stubs via `make proto && git diff --exit-code`.
- **[Audio frame overhead]** Wrapping PCM in `AudioData` adds ~3 bytes per chunk. → *Mitigation*: 0.5% overhead on a 640-byte PCM chunk is negligible. BLE bandwidth is the bottleneck, not protocol overhead.
- **[Structured logging requires `structlog` on Python]** New runtime dep. → *Mitigation*: `structlog` is pure Python, no native deps, ~50KB installed. Already used in Hermes-adjacent projects.
- **[Debug CLI doesn't fix the underlying bug by itself]** The bug might still be in the JS handshake, the TLS layer, or a Tailscale network issue. → *Mitigation*: the migration forces every code path to log its state. Whatever the bug is, it'll show up in the logs on first reproduction.

## Migration Plan

1. Add `.proto` + `buf.gen.yaml`. Generate stubs. Verify they compile on both sides without changing call sites.
2. Add `wire.py` / `wire.ts` wrappers re-exporting constructor names.
3. Add `structlog` setup and structured log statements to `server.py` connection lifecycle. **Do not migrate frames yet** — just add logging to the existing JSON path. Reproduce the connect-then-disconnect bug from logs. (This step might already find the bug.)
4. Migrate `server.py` dispatch loop to Protobuf. Migrate `main.ts` dispatcher to Protobuf. Both sides now speak the new wire format. Run full test suite.
5. Add `debug_client.py`.
6. Delete `protocol.py`, `protocol_gen.py`, `glasses-app/src/protocol.ts`. Mark generated dirs as build artifacts in `.gitignore` exclusion lists.
7. CI gate: `make proto && git diff --exit-code` (catches stale stubs).
8. Manual end-to-end: glasses-app + plugin + debug CLI against a real bridge instance.

Steps 1-3 can land as one commit (logging + scaffolding). Step 4 is the big atomic change. Steps 5-7 are follow-up commits.

## Open Questions

None. All decisions made; the rest is captured in `tasks.md`.
