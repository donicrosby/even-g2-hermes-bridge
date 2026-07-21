## ADDED Requirements

### Requirement: Plugin registers handlers for gateway session lifecycle hooks

The plugin SHALL register handlers for the Hermes Gateway plugin hooks `on_session_start`, `on_session_end`, `on_session_finalize`, and `on_session_reset` via `ctx.register_hook(...)` in `plugin/src/byoa_plugin/hooks.py:bind()`. Registration SHALL be wrapped in `try/except (AttributeError, TypeError)` and log a warning on failure, matching the existing `pre_tool_call`/`post_tool_call` registration pattern. All handlers SHALL accept `**kwargs` for forward compatibility per upstream contract.

Rationale: the Hermes Gateway (`gateway/session.py` `SessionStore`) is the source of truth for sessions. It emits lifecycle hooks so adapters can translate events into client-facing frames without owning session state. This is the canonical pattern documented at https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks.

#### Scenario: Plugin loads on a gateway that emits session hooks
- **WHEN** the plugin's `register(ctx)` runs against a Hermes Gateway that supports session hooks
- **THEN** all four session hook handlers SHALL be registered
- **AND** a debug log entry SHALL confirm each registration

#### Scenario: Plugin loads on an older gateway without session hooks
- **WHEN** `ctx.register_hook("on_session_start", ...)` raises `AttributeError` or `TypeError`
- **THEN** the plugin SHALL log a warning naming the missing hook
- **AND** SHALL continue to load and operate without session-hook support
- **AND** other hooks (`pre_tool_call`, `post_tool_call`) SHALL still be registered

### Requirement: on_session_start and on_session_reset emit active frames

The `on_session_start` and `on_session_reset` handlers SHALL, when invoked:
1. Read `adapter._last_chat_id` (the most recent inbound chat_id, or `None` if no inbound frame has arrived yet).
2. If `_last_chat_id` is `None`, log a debug message and return without emitting a frame.
3. Otherwise, store `adapter._session_by_chat[_last_chat_id] = session_id` so the existing `chat_for_session` reverse lookup (used by tool-call hooks at `hooks.py:106,148`) starts working.
4. Emit `proto.active(session_id, name=session_id[:16])` to `_last_chat_id` via `adapter.registry.send_frame(...)`.

Both hooks treat their `session_id` identically — a reset creates a new session id; the glasses see the same `active` frame either way.

#### Scenario: First session after glasses connect
- **WHEN** the glasses pair on `chat_id="g2-1"` sends a text message and the gateway creates session `"s-abc123"` for it
- **THEN** the plugin's `on_session_start` handler SHALL fire with `session_id="s-abc123"`
- **AND** the plugin SHALL record `_session_by_chat["g2-1"] = "s-abc123"`
- **AND** the plugin SHALL emit an `active` frame with `id="s-abc123"` and `name="s-abc123"` (or its first 16 chars) to `chat_id="g2-1"`

#### Scenario: on_session_start fires before any inbound frame
- **WHEN** the gateway emits `on_session_start` but no inbound frame has set `adapter._last_chat_id` yet
- **THEN** the plugin SHALL log a debug message (`"on_session_start: no last_chat_id; skipping"`) and not emit a frame
- **AND** SHALL NOT raise an exception

#### Scenario: on_session_reset fires after /new
- **WHEN** the glasses send `sessions.new` → adapter forwards `/new` → gateway processes → gateway emits `on_session_reset` with `session_id="s-def456"`
- **THEN** the plugin SHALL emit an `active` frame with `id="s-def456"` and `name="s-def456"[:16]` to the last active chat_id
- **AND** SHALL update `_session_by_chat[last_chat_id] = "s-def456"`

### Requirement: on_session_end logs without emitting a frame

The `on_session_end` handler SHALL accept `session_id`, `completed`, `interrupted`, `model`, `platform`, and `**kwargs`. It SHALL log an info-level entry and SHALL NOT emit any frame to the glasses. Rationale: end-of-turn is not a UI-visible session change; the glasses-app has no use for this event in v1.

#### Scenario: Normal end of an assistant turn
- **WHEN** the gateway finishes a `run_conversation()` call and emits `on_session_end(session_id="s-1", completed=True, interrupted=False, ...)`
- **THEN** the plugin SHALL log an info entry with the session_id, completed, and interrupted fields
- **AND** SHALL NOT call `adapter.registry.send_frame(...)`

### Requirement: on_session_finalize cleans up the reverse mapping

The `on_session_finalize` handler SHALL accept `session_id` (which may be `None`), `platform`, and `**kwargs`. If `session_id` is not `None` and is present in `adapter._session_by_chat` values, the handler SHALL remove the entry. No frame is emitted.

#### Scenario: Gateway finalizes a known session
- **WHEN** `_session_by_chat = {"g2-1": "s-1"}` and `on_session_finalize(session_id="s-1", platform="even-g2")` fires
- **THEN** the plugin SHALL remove the `"g2-1"` entry from `_session_by_chat`
- **AND** SHALL NOT emit a frame

#### Scenario: Gateway finalizes an unknown or None session
- **WHEN** `on_session_finalize(session_id=None, ...)` or `session_id="unknown"` fires
- **THEN** the plugin SHALL do nothing (no mapping change, no frame, no error)

### Requirement: Adapter tracks last_chat_id on every inbound frame

The adapter SHALL maintain `self._last_chat_id: str | None` (initially `None`). Every inbound handler (`_on_text`, `_on_audio_stop`, `_on_sessions_list`, `_on_sessions_switch`, `_on_sessions_new`) SHALL set `self._last_chat_id = chat_id` as its first statement. This pointer is the only mechanism the session hooks have to attribute an event to a glasses pair, because the upstream hook payload does not include `chat_id`.

#### Scenario: First inbound frame sets the pointer
- **WHEN** `_on_text("g2-1", "hello")` runs as the first inbound frame after plugin start
- **THEN** `_last_chat_id` SHALL become `"g2-1"` before the handler proceeds

#### Scenario: Subsequent frames update the pointer
- **WHEN** `_last_chat_id = "g2-1"` and `_on_sessions_switch("g2-2", "+1")` runs (hypothetical second pair)
- **THEN** `_last_chat_id` SHALL become `"g2-2"`

### Requirement: hello.ok includes active when known, omits when unknown

The plugin's WS server SHALL include `active=<session_id>` in `hello.ok` when `adapter.session_for_chat(chat_id)` returns a non-None value for the connecting chat_id. When the lookup returns `None` (first-ever connect, no prior session for this chat_id), the server SHALL omit `active=` from `hello.ok` entirely (per the existing `proto.hello_ok(active=None)` constructor behavior at `protocol.py:96`). The server SHALL NOT synthesise a fake or placeholder session id.

#### Scenario: Returning connection reuses a known session
- **WHEN** the glasses pair on `chat_id="g2-1"` reconnects and `_session_by_chat["g2-1"] = "s-1"`
- **THEN** the `hello.ok` frame SHALL include `active="s-1"`

#### Scenario: First-ever connection has no known session
- **WHEN** the glasses pair on `chat_id="g2-1"` connects for the first time and `session_for_chat("g2-1")` returns `None`
- **THEN** the `hello.ok` frame SHALL omit the `active` field
- **AND** the glasses-app SHALL leave `currentSessionId` unchanged per existing behavior
- **AND** the next inbound message SHALL trigger `on_session_start`, populating the glasses UI via an `active` frame

### Requirement: sessions.list emits a single-entry sessions frame

When the glasses sends `sessions.list`, the adapter SHALL respond with a `sessions` frame whose `items` array contains a single entry derived from `_session_by_chat[chat_id]` (if present) — `{id: <session_id>, name: <session_id[:16]>}` — and whose `active` field is the same session id. If no session is bound for this chat_id, the adapter SHALL respond with an empty `items` array and no `active` field. The existing `/sessions` slash-command forwarding to the gateway SHALL still happen (best-effort sync; the gateway's reply renders in the assistant container per existing behavior).

Rationale: the glasses-app needs SOMETHING to render on connect even before the user's first message triggers a real session. With only the bound session known, the single-entry list is the honest answer. A future change can enrich this when the plugin learns how to query the gateway's full session list.

#### Scenario: sessions.list after a session is bound
- **WHEN** `chat_id="g2-1"` has `_session_by_chat["g2-1"] = "s-1"` and sends `sessions.list`
- **THEN** the plugin emits a `sessions` frame with `items=[{id:"s-1", name:"s-1"}]` and `active="s-1"`

#### Scenario: sessions.list before any session is bound
- **WHEN** `chat_id="g2-1"` has no entry in `_session_by_chat` and sends `sessions.list`
- **THEN** the plugin emits a `sessions` frame with `items=[]` and no `active` field
