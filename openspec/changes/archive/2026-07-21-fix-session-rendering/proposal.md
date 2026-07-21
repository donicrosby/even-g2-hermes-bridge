## Why

User reports: "nothing shows up when I load the app and the new version of the plugin." Root cause confirmed by code reading:

1. **`plugin/src/byoa_plugin/server.py:155`** sends `proto.hello_ok(caps=caps)` **without `active=`**. The glasses-app's `handleHelloOk` (`glasses-app/src/main.ts:281-288`) checks `if (frame.active)` — falsy → `currentSessionId` stays `''` → `renderSession()` writes a single space → session container appears empty.
2. **The plugin never emits `active` or `sessions` frames anywhere.** Grep across `plugin/src/byoa_plugin/` returns zero hits for `proto.active(` and `proto.sessions(`. The adapter (`adapter.py:253-281`) forwards `sessions.list`/`sessions.switch`/`sessions.new` to the gateway as plain-text slash commands (`/sessions`, `/resume <id>`, `/new`). The gateway's reply comes back through `EvenG2Adapter.send()` → `proto.assistant_delta()` → renders in the assistant container, never as a structured session frame.
3. **The glasses-app never calls `sendSessionsList` on connect.** Only `switchSession(delta)` on scroll.
4. **Bonus bug discovered during investigation**: `_session_by_chat` is never populated, so `hooks.py:106,148` (`chat_for_session(session_id)`) always returns `None` and `tool.start`/`tool.end` frames are silently dropped with a debug log. Tool-call visibility is broken on the glasses too.

This is a known unfinished seam from the prior `build-even-g2-hermes-platform` change — task 12.8 (`Test: scroll to switch sessions`) was never checked off because the end-to-end flow was never wired.

## What Changes

**Pivot from the original "plugin-local session list" idea to the Hermes-canonical pattern: subscribe to gateway session lifecycle hooks.** Investigation (via upstream source at NousResearch/hermes-agent) confirmed the gateway emits `on_session_start`, `on_session_end`, `on_session_finalize`, `on_session_reset` plugin hooks (see `hermes_cli/plugins.py` and the [Event Hooks docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)). Our existing `hooks.py` already registers `pre_tool_call`/`post_tool_call` via the same `ctx.register_hook(...)` mechanism at line 38 — we just add session hooks alongside.

- **Add** session-hook handlers in `plugin/src/byoa_plugin/hooks.py`:
  - `on_session_start(session_id, model, platform, **kwargs)` → record `_session_by_chat[_last_chat_id] = session_id`, emit `proto.active(session_id, name=session_id[:16])` to `_last_chat_id`.
  - `on_session_reset(session_id, platform, **kwargs)` → same as `on_session_start` (a reset creates a new session id; treat identically).
  - `on_session_end(session_id, completed, interrupted, model, platform, **kwargs)` → log only; no frame (no UI action needed at end of a turn).
  - `on_session_finalize(session_id, platform, **kwargs)` → if `session_id` is in `_session_by_chat` values, remove the entry (housekeeping).
- **Register** all four hooks in `hooks.py:bind()` alongside the existing `pre_tool_call`/`post_tool_call` registrations.
- **Maintain `_last_chat_id`** in the adapter — updated every time an inbound frame arrives (`_on_text`, `_on_audio_stop`, `_on_sessions_list`, `_on_sessions_switch`, `_on_sessions_new`). The session hooks don't receive a `chat_id` in their payload (confirmed in upstream signature), so this pointer is how we attribute a session event to a glasses pair. Multi-pair is a documented v1 limitation.
- **Fix** `plugin/src/byoa_plugin/server.py:155` to include `active=<last known session_id for this chat_id>` in `hello.ok`. If unknown (first connect), omit and let the next `on_session_start` populate the glasses UI.
- **Keep** the existing slash-command forwarding in the adapter (`/sessions`, `/resume`, `/new`). That's how the user requests session changes; the hook events drive the structured frame emission back to the glasses. **Glasses-app** sends `sessions.switch`/`sessions.new` → adapter forwards as `/resume`/`/new` → gateway processes via `SessionStore.switch_session`/`reset_session` → gateway emits `on_session_reset`/`on_session_start` → plugin hook emits `active` frame to glasses.
- **Fix** `glasses-app/src/main.ts` to call `sendSessionsList()` once after `handleHelloOk` lands. Server responds with a `sessions` frame synthesised from `_session_by_chat` (single-entry for v1).
- **Add** plugin-side integration tests that simulate the hook callbacks firing and assert the right frames land on the right chat_id.
- **Add** glasses-app tests for `handleHelloOk` + `sendSessionsList` sequencing and `handleSessions` dispatcher case.
- **No changes** to the WS wire protocol (`protocol.py` schemas already cover everything), no changes to `bridge-server/`, no new containers.

## Capabilities

### New Capabilities
- `glasses-app-session-roundtrip`: Defines the glasses-app side of the session contract — request the session list on connect, render the initial `active` frame, and update on subsequent `active`/`sessions` frames. Covers the `sendSessionsList`-on-connect addition and the existing `handleActive`/`handleHelloOk` behavior that was specced in `build-even-g2-hermes-platform` but never fully implemented.
- `plugin-session-hooks`: Defines the plugin-side session contract — subscribe to gateway `on_session_*` hooks, maintain `_last_chat_id` + `_session_by_chat`, emit `active`/`sessions` frames on hook events, and include `active=` in `hello.ok`. Covers the hook handler additions to `hooks.py`, the adapter pointer, and the server fix.

### Modified Capabilities
<!-- None — openspec/specs/ has no capability covering session emission today. The two new capabilities above are net-additive. -->

## Impact

- **Code**: ~80 lines added to `plugin/src/byoa_plugin/hooks.py` (4 hook handlers + 4 `register_hook` calls + helper to resolve chat_id), ~15 lines added to `plugin/src/byoa_plugin/adapter.py` (`_last_chat_id` tracking on every inbound handler), ~5 lines changed in `plugin/src/byoa_plugin/server.py` (pass active into `hello_ok`), ~10 lines added to `glasses-app/src/main.ts` (`sendSessionsList` call + `handleSessions` case + `knownSessions` state). ~120 lines of new pytest tests in `plugin/tests/test_session_hooks.py`. ~40 lines of new Vitest tests in `glasses-app/tests/session-roundtrip.test.ts`.
- **No new module** — the originally-proposed `sessions.py` is dropped. The gateway's `SessionStore` is the source of truth.
- **Dependencies**: None new.
- **Behavior**: User-visible — session container shows a (truncated) session id on app load; scroll/new-session updates it; tool-call frames now also reach the glasses (bonus bug fix from populating `_session_by_chat`).
- **Testing**: `cd plugin && uv run pytest` (currently 123 tests; ~6 new). `cd glasses-app && npm run test` (currently 60; ~3 new).
- **Rollback**: Pure local revert of the touched files.
- **Non-goals**: Multi-pair chat_id attribution (single `_last_chat_id` per adapter for v1). Human-readable session names (hooks don't provide them; truncated session id is the v1 fallback). Persisting session lists across plugin restarts. Touching `bridge-server/`. Touching the un-archived `build-even-g2-hermes-platform` change.
