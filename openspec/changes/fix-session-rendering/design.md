## Context

The session-rendering bug has three independent causes (plus a fourth bonus bug):

1. `server.py:155` sends `proto.hello_ok(caps=caps)` with no `active=`. Glasses-app's `handleHelloOk` reads `frame.active`, finds it undefined, skips `renderSession()`.
2. The adapter's session handlers (`adapter.py:253-281`) forward `/sessions`, `/resume <id>`, `/new` to the gateway as plain text via `handle_message`. The gateway's reply comes back through `EvenG2Adapter.send()` → `proto.assistant_delta()` → renders in the assistant container. The structured `active`/`sessions` frames the glasses-app expects never get emitted.
3. The glasses-app never calls `sendSessionsList` on connect.
4. **Bonus**: `_session_by_chat` is never populated, so `hooks.py:106,148` (`chat_for_session`) always returns `None` and `tool.start`/`tool.end` frames are silently dropped.

## Upstream contract (verified)

The Hermes Gateway (NousResearch/hermes-agent) exposes plugin hooks via `ctx.register_hook(name, callback)` in the plugin's `register()` function. We already use this for tool-call hooks at `hooks.py:38`. The relevant session hooks (per [Event Hooks docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks) and `hermes_cli/plugins.py`):

| Hook name | When it fires | Payload kwargs |
|---|---|---|
| `on_session_start` | New session created (first turn of a new or resumed session) | `session_id: str`, `model: str`, `platform: str`, `**kwargs` |
| `on_session_end` | End of every `run_conversation()` call | `session_id: str`, `completed: bool`, `interrupted: bool`, `model: str`, `platform: str`, `**kwargs` |
| `on_session_finalize` | Gateway tears down an active session (after `/new`, `/reset`, idle GC, CLI quit) | `session_id: str \| None`, `platform: str`, `**kwargs` |
| `on_session_reset` | Gateway swaps in a fresh session key (after `/new`, `/reset`) | `session_id: str`, `platform: str`, `**kwargs` |

**Hard constraints from upstream:**
- **No `chat_id` in any hook payload.** Only `session_id`, `platform`, and per-hook extras (`model`, `completed`, `interrupted`). The plugin cannot directly know which glasses pair a session belongs to.
- **No display name/title in any hook payload.** Just IDs.
- **All callbacks should accept `**kwargs**` for forward compatibility.
- `session_id` may be `None` in `on_session_finalize` (if no active session existed).

## Goals / Non-Goals

**Goals:**
- Show *something* (a truncated session id) on the session container on app load.
- Update the session id when the user switches or creates a session.
- Restore tool-call frame delivery to the glasses (bonus bug fix).
- Stay aligned with the Hermes hook contract — don't reinvent `SessionStore`.

**Non-Goals:**
- Human-readable session names (upstream doesn't provide them in hooks; truncated session id is the v1 fallback).
- Multi-pair chat_id attribution (single `_last_chat_id` per adapter for v1; documented limitation).
- Persisting sessions across plugin restarts.
- Touching `bridge-server/` or the un-archived `build-even-g2-hermes-platform` change.
- Adding new wire-protocol frame types.

## Decisions

### D1: Subscribe to gateway session hooks; do NOT maintain a plugin-local session list

**Choice.** Register `on_session_start`, `on_session_end`, `on_session_finalize`, `on_session_reset` handlers via `ctx.register_hook(...)` in `hooks.py:bind()`. On `on_session_start` / `on_session_reset`, emit `proto.active(session_id, name=session_id[:16])` to the appropriate chat_id. The gateway's `SessionStore` remains the single source of truth.

**Rationale.** This is the canonical Hermes pattern. The prior plan (plugin-local `SessionList` module) would have fixed the user's bug but created a parallel truth that drifts whenever the gateway adds/expires/renames sessions out of band. Investigation confirmed: the gateway has a real session store at `gateway/session.py` with `switch_session`/`reset_session`/`get_or_create_session`, and it emits structured lifecycle hooks specifically so adapters don't have to guess.

**Alternatives considered (rejected).**
- **Plugin-local session list as source of truth.** Rejected: diverges from gateway truth, untestable against real gateway behavior, violates the "adapters translate gateway events, they don't own state" pattern that the existing `pre_tool_call`/`post_tool_call` hook wiring demonstrates.
- **Parse gateway reply to `/sessions`.** Rejected: untestable, brittle, gateway-dependent.
- **Use the API server adapter's REST endpoints (`GET /api/sessions`).** Rejected: requires the API server to be enabled on the gateway host, adds an HTTP dependency, and still doesn't give us push notifications. Hooks are push; REST is pull.

### D2: Track `_last_chat_id` to attribute session events to a glasses pair

**Choice.** Add `self._last_chat_id: str | None = None` to the adapter. Every inbound handler (`_on_text`, `_on_audio_stop`, `_on_sessions_list`, `_on_sessions_switch`, `_on_sessions_new`) sets `self._last_chat_id = chat_id` as its first statement. Session hook handlers read `adapter._last_chat_id` to know where to send the frame.

**Rationale.** Upstream hook payloads don't include `chat_id` (verified). The only way to attribute a session event to a glasses pair is to remember which chat_id most recently sent an inbound frame. For v1 (single-user, single-pair per tailnet per the prior change's design), this is correct. The adapter already maintains `_session_by_chat: dict[str, str]` — we keep it and populate it from the hooks (replacing the never-called `bind_session` method's intended role).

**Alternatives considered.**
- **Embed chat_id in `MessageEvent.metadata` and pray the gateway preserves it into the hook context.** Rejected: upstream docs don't confirm metadata survives into plugin hook payloads; can't verify without running against a live gateway.
- **Stash chat_id in a `contextvars.ContextVar` set by `_on_*` handlers.** Rejected: hooks fire asynchronously from a different task; contextvars don't propagate across task boundaries without explicit plumbing.
- **Assume one chat_id globally (drop the dict, use a single scalar).** Rejected: minor simplification that costs us the existing `chat_for_session` reverse lookup. Keep the dict; populate it from hooks.

### D3: Use truncated `session_id` as the display name

**Choice.** When emitting `proto.active(session_id, name=...)`, set `name = session_id[:16]` (matches the "fall back to first 16 chars of session ID" rule from `build-even-g2-hermes-platform/specs/glasses-ws-app/spec.md`).

**Rationale.** Upstream hooks don't provide a display name. The prior change's spec already anticipated this fallback. Truncated UUID-ish session ids aren't pretty but they're honest and they're unique. A future change can swap in richer names if/when upstream adds a `display_name` kwarg.

**Alternative considered.** Synthesize a name like `Session N` from a counter. Rejected: requires maintaining a counter (state), and the counter wouldn't survive plugin restart anyway. Truncated id is stateless.

### D4: Populate `_session_by_chat` from `on_session_start`/`on_session_reset`

**Choice.** In the `on_session_start` and `on_session_reset` handlers, after looking up `chat_id = adapter._last_chat_id`, set `adapter._session_by_chat[chat_id] = session_id`. This repairs the existing `chat_for_session` lookup that `hooks.py:106,148` depends on for tool-call routing.

**Rationale.** Today the mapping is empty (nobody calls `bind_session`), so tool-call frames silently drop. Populating it from the session hooks is the natural fix — and exactly what `bind_session` was supposed to be used for. Leave the `bind_session` method in place (don't break hypothetical external callers); the hooks just write to the same dict directly.

### D5: `hello.ok` includes `active=` only when known

**Choice.** In `server.py` after auth succeeds, look up `adapter.session_for_chat(chat_id)`. If it returns a value, send `hello_ok(active=that_value, caps=caps)`. If it returns `None` (first-ever connect), send `hello_ok(caps=caps)` only. Do NOT synthesise a fake session id — the next inbound message will trigger `on_session_start` and the glasses will get a real `active` frame.

**Rationale.** Lying in `hello.ok` would set `currentSessionId` to a value the gateway doesn't know about. Better to send no `active` and let the real hook event populate the glasses UI on the user's first interaction. The cost is a brief window (one frame) where the session container shows blank after a fresh connect — acceptable, and honest.

**Alternative considered.** Send a synthetic `active="pending"` to make the container non-blank. Rejected: lies to the glasses-app; downstream code might try to send `sessions.switch` against a non-existent id.

## Risks / Trade-offs

- **[Single-pair attribution]** If two glasses pairs connect to the same plugin (different `chat_id`s), `_last_chat_id` flips between them on every inbound frame, and session events may route to the wrong pair. → *Mitigation*: documented v1 limitation per the prior change's design (`D6: chat_id = device serial; each pair gets its own session`). Multi-pair is out of scope. A future change can thread chat_id through `MessageEvent.metadata` if upstream preserves it, or switch to gateway hooks (which DO include `session_key` = platform-specific identifier).
- **[No human-readable names]** Session container shows a 16-char id snippet. → *Mitigation*: acceptable per prior spec. Future change can enrich when upstream hooks gain a name field.
- **[Hooks depend on gateway emitting them]** If the gateway is older than the hook introduction (or a fork doesn't emit them), the glasses never gets `active` frames. → *Mitigation*: register hooks defensively (`try: ctx.register_hook(...) except (AttributeError, TypeError)`) per the existing pattern at `hooks.py:38-42`. Log a warning on failure; the rest of the plugin still works.
- **[`on_session_end` is high-frequency]** It fires at the end of every `run_conversation()` (every assistant turn). → *Mitigation*: log only, no frame emission. The glasses doesn't need a UI update at end-of-turn.

## Migration Plan

1. Land hook-handler additions to `hooks.py` + tests (pure logic; defensive registration).
2. Land adapter `_last_chat_id` tracking + populate `_session_by_chat` from hooks.
3. Land `server.py:155` `hello_ok(active=...)` fix.
4. Land glasses-app `sendSessionsList` + `handleSessions`.
5. CI gates: `uv run pytest`, `uv run ruff check`, `uv run basedpyright`, `npm run test`, `npm run typecheck`, `npm run lint`, `npm run build`.
6. No on-device migration; wire protocol unchanged.
7. Rollback: `git revert`. Reverts to today's "no sessions render" behavior (the bug).

## Open Questions

None. Hook signatures verified; all decisions made.
