## 1. Session hook handlers (plugin/src/byoa_plugin/hooks.py)

- [x] 1.1 Add a private `_resolve_chat_id(adapter) -> str | None` helper that returns `adapter._last_chat_id`. Logs at debug level and returns `None` if the pointer is unset. This is the single point where the no-chat_id-in-hook-payload limitation is papered over.
- [x] 1.2 Add `_emit_active_frame(adapter, chat_id, session_id) -> None` helper that calls `adapter.registry.send_frame(chat_id, proto.active(session_id, name=session_id[:16]))` via the same `asyncio.get_event_loop()` + `run_coroutine_threadsafe` / `create_task` pattern used by `_pre_tool_call` at lines 118-129. Centralises the frame-emission plumbing so all four handlers stay tiny.
- [x] 1.3 Implement `_on_session_start(*, session_id: str, model: str, platform: str, **_: object) -> None`:
  - `adapter = _get_adapter()`; return if None.
  - `chat_id = _resolve_chat_id(adapter)`; log debug + return if None.
  - `adapter._session_by_chat[chat_id] = session_id` (repairs `chat_for_session` for tool-call hooks).
  - `_emit_active_frame(adapter, chat_id, session_id)`.
- [x] 1.4 Implement `_on_session_reset(*, session_id: str, platform: str, **_: object) -> None` — identical body to `_on_session_start` (a reset creates a new session id; the glasses see the same `active` frame either way). Either inline the same logic or have `_on_session_reset` delegate to a shared `_record_and_emit(adapter, session_id)` helper that both call.
- [x] 1.5 Implement `_on_session_end(*, session_id: str, completed: bool, interrupted: bool, model: str, platform: str, **_: object) -> None` — log info only (`LOG.info("session end sid=%s completed=%s interrupted=%s", session_id, completed, interrupted)`). No frame emission.
- [x] 1.6 Implement `_on_session_finalize(*, session_id: str | None, platform: str, **_: object) -> None`:
  - If `session_id is None`, return.
  - Look up the chat_id via the existing reverse map: iterate `adapter._session_by_chat.items()` and find any entry whose value equals `session_id`. (There should be at most one for v1.)
  - If found, `del adapter._session_by_chat[chat_id]`. Log debug. No frame.
- [x] 1.7 Extend `bind(ctx)` (line 28-42) to register all four new hooks inside the existing `try` block:
  ```
  ctx.register_hook("on_session_start", _on_session_start)
  ctx.register_hook("on_session_reset", _on_session_reset)
  ctx.register_hook("on_session_end", _on_session_end)
  ctx.register_hook("on_session_finalize", _on_session_finalize)
  ```
  Keep the existing `except (AttributeError, TypeError)` so a gateway that lacks any of these hooks just logs a warning and continues. Update the success log to mention all 6 hooks.
- [x] 1.8 Confirm via `grep -n "_session_by_chat" plugin/src/byoa_plugin/` that no other code mutates the dict after this change — only `_on_session_start`/`_on_session_reset`/`_on_session_finalize` write to it.

## 2. Adapter: track last_chat_id

- [x] 2.1 In `plugin/src/byoa_plugin/adapter.py`, add `self._last_chat_id: str | None = None` near the existing `_session_by_chat` declaration (around line 158).
- [x] 2.2 As the first statement of `_on_text`, `_on_audio_stop`, `_on_sessions_list`, `_on_sessions_switch`, `_on_sessions_new` (lines 212, 222, 253, 263, 273), set `self._last_chat_id = chat_id`. Five one-line additions.
- [x] 2.3 Keep the existing `bind_session`, `session_for_chat`, `chat_for_session` helpers (lines 329-342) unchanged. Do NOT remove them — `chat_for_session` is actively used by `hooks.py:106,148` for tool-call routing, and `_on_session_start` now writes to the same dict they read from.
- [x] 2.4 Add a brief inline comment on `_last_chat_id` explaining why it exists: "Plugin hooks don't receive chat_id in their payload (verified upstream); this pointer is the only way to attribute a session event to a glasses pair. v1 limitation: single-pair per adapter."

## 3. Server: hello.ok includes active when known

- [x] 3.1 In `plugin/src/byoa_plugin/server.py`, extend `BridgeServer.__init__` to accept an `active_session_lookup: Callable[[str], str | None] | None = None` callback. Store as `self._active_session_lookup`. (Or, equivalently, pass a reference to the adapter itself — but the lookup callable is more testable and avoids a circular import.)
- [x] 3.2 Around line 150-155 in `_handle_connection`, after `chat_id` is computed and auth passes, call `active = self._active_session_lookup(chat_id) if self._active_session_lookup else None`. Then `await ws.send(proto.hello_ok(active=active, caps=caps))`. The existing `proto.hello_ok` constructor at `protocol.py:96` already handles `active=None` correctly (omits the field).
- [x] 3.3 In `EvenG2Adapter.connect()` (around line 176-185), wire `active_session_lookup=self.session_for_chat` into the `BridgeServer(...)` constructor. (`session_for_chat` already exists at adapter.py:333 and returns `str | None`.)

## 4. Plugin integration tests (plugin/tests/test_session_hooks.py)

- [x] 4.1 Create `plugin/tests/test_session_hooks.py`. Use the existing pytest patterns from `test_tool_label.py` (which already exercises hook-style code) as the template.
- [x] 4.2 Test `_on_session_start` happy path: build a fake adapter (`SimpleNamespace(_last_chat_id="g2-1", _session_by_chat={}, registry=SimpleNamespace(send_frame=AsyncMock())))`, call `_on_session_start(session_id="s-abc", model="m", platform="even-g2")`, assert `_session_by_chat["g2-1"] == "s-abc"` and `registry.send_frame` was awaited once with arguments matching `proto.active("s-abc", name="s-abc")`.
- [x] 4.3 Test `_on_session_start` with no `_last_chat_id`: assert no frame sent, no exception, debug log emitted (use `caplog`).
- [x] 4.4 Test `_on_session_reset` behaves identically to `_on_session_start` for the same inputs.
- [x] 4.5 Test `_on_session_end` logs and emits no frame: assert `registry.send_frame` was NOT called.
- [x] 4.6 Test `_on_session_finalize` removes the reverse mapping: pre-populate `_session_by_chat={"g2-1": "s-1"}`, call `_on_session_finalize(session_id="s-1", platform="even-g2")`, assert `_session_by_chat == {}`.
- [x] 4.7 Test `_on_session_finalize` with `session_id=None` is a no-op.
- [x] 4.8 Test `_on_session_finalize` with an unknown `session_id` is a no-op.
- [x] 4.9 Test `_resolve_chat_id` returns the adapter's `_last_chat_id` and tolerates `None`.
- [x] 4.10 Run `cd plugin && uv run pytest tests/test_session_hooks.py -q` and confirm all tests pass.

## 5. Plugin end-to-end test (server hello.ok wiring)

- [x] 5.1 Add a test to `plugin/tests/test_integration_ws.py` (existing WS harness) that asserts `hello.ok` includes `active=<session_id>` when the lookup callback returns one.
- [x] 5.2 Add a parallel test that asserts `hello.ok` omits `active` when the lookup returns `None`.
- [x] 5.3 Run `cd plugin && uv run pytest -q` and confirm the entire suite (existing 123 + new ~10) passes.
- [x] 5.4 Run `cd plugin && uv run ruff check src/ tests/` — zero issues.
- [x] 5.5 Run `cd plugin && uv run basedpyright` — zero issues.

## 6. Glasses-app changes

- [x] 6.1 In `glasses-app/src/main.ts:handleHelloOk`, after the existing logic, add `sendFrame({ t: 'sessions.list' });`. Confirm `OutboundClientFrame` already includes `SimpleInboundFrame` (which covers `'sessions.list'`) — yes per `glasses-app/src/protocol.ts:38-40` — so no type change needed.
- [x] 6.2 Add a new `handleSessions(frame: SessionsFrame)` function that: (a) stores the items list in a new module-level `let knownSessions: Array<{ id: string; name?: string }> = [];`, (b) if `frame.active` is present AND differs from `currentSessionId`, updates `currentSessionId` and `currentSessionName` (looked up from `knownSessions` by id) and calls `renderSession()` + `scheduleSave()`, (c) if `frame.active` matches or is absent, just stores the list (no re-render). Import `SessionsFrame` from `./protocol` (already exported).
- [x] 6.3 Add `case 'sessions': handleSessions(frame as unknown as SessionsFrame); break;` to the `handleFrame` switch (between `tool.end` and `transcript` cases for readability — order doesn't matter functionally).
- [x] 6.4 Initialize `knownSessions = []` near the other module-level mutable state (lines 67-77). Restore it from local storage in `restoreState()` (extend `GlassesAppState` in `src/lib/state.ts` with `knownSessions?: Array<{ id: string; name?: string }>` and `mergeState` to default it to `[]` when missing). Update existing `state.test.ts` to cover the new field's absence and presence.
- [x] 6.5 Write `glasses-app/tests/session-roundtrip.test.ts` covering: (a) `handleHelloOk` with `active: "s1"` calls `sendFrame({t:'sessions.list'})` once (mock `ws.send`), (b) `handleSessions` with a list that includes the active id updates `currentSessionName` from the item's name, (c) `handleSessions` with `active` matching current state does not re-render. Use the same Vitest fake-WS pattern from existing tests.

## 7. End-to-end manual verification

- [x] 7.1 Run `cd plugin && uv run python -m byoa_plugin.protocol_gen > ../glasses-app/src/protocol.ts` and `git diff` — should be empty (no protocol changes). If non-empty, investigate.
- [x] 7.2 Run `cd plugin && uv run pytest -q && cd ../glasses-app && npm run test && npm run typecheck && npm run lint && npm run build`. All green.
- [x] 7.3 Sanity-read the final `hooks.py` end-to-end: confirm all four session hooks are registered defensively, all handlers tolerate `None` `_last_chat_id`, and `_session_by_chat` mutations only happen in `_on_session_start`/`_on_session_reset`/`_on_session_finalize`.
- [x] 7.4 Sanity-read the final `adapter.py`: confirm `_last_chat_id` is set as the first statement of every `_on_*` handler, and that `session_for_chat` is correctly wired into `BridgeServer(active_session_lookup=...)`.

## 8. OpenSpec wrap

- [x] 8.1 Run `openspec validate fix-session-rendering` and fix any reported issues.
- [x] 8.2 Stage and commit atomically across three commits per AGENTS.md convention: (1) `feat(plugin): subscribe to gateway session lifecycle hooks` — hooks.py + adapter.py + server.py + plugin tests; (2) `feat(glasses-app): request session list on connect and handle sessions frame` — main.ts + state.ts + session-roundtrip.test.ts + state.test.ts; (3) `docs(openspec): add fix-session-rendering change` — openspec change artifacts. Pair tests with implementation in each commit.
