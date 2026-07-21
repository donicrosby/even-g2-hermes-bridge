## ADDED Requirements

### Requirement: Glasses-app requests the session list on connect

The glasses-app SHALL send a `sessions.list` frame immediately after processing `hello.ok`. The frame SHALL be sent exactly once per successful hello (not on reconnect attempts that fail the handshake). The plugin's response (a `sessions` frame) populates the glasses-app's local session list for scroll cycling.

#### Scenario: First connection triggers sessions.list
- **WHEN** the glasses-app receives `hello.ok` after a fresh WS open
- **THEN** the glasses-app SHALL send `{t: 'sessions.list'}` over the open WebSocket
- **AND** SHALL NOT send `sessions.list` again until the next successful hello

#### Scenario: Failed handshake does not trigger sessions.list
- **WHEN** the server closes the WS with code 1008 (auth failed)
- **THEN** the glasses-app SHALL NOT have sent `sessions.list`
- **AND** the glasses-app SHALL set status to "Auth failed" per existing behavior

### Requirement: Glasses-app renders active session name from hello.ok + active frame

On receiving `hello.ok` with an `active` field, the glasses-app SHALL set `currentSessionId` from that field and render the session container with whatever name state it has (possibly none yet). On receiving a subsequent `active` frame with a `name` field, the glasses-app SHALL update `currentSessionName`, re-render the session container with the new name, and persist state via `scheduleSave()`.

#### Scenario: hello.ok then active frame renders the name
- **WHEN** the glasses-app receives `hello.ok` with `active="s1"` followed immediately by `active` with `id="s1"` and `name="Default"`
- **THEN** the session container SHALL first render `' '` (no name yet, just the id-state set internally)
- **AND** then render `Default` once the `active` frame lands

#### Scenario: hello.ok without active field preserves prior state
- **WHEN** the glasses-app receives `hello.ok` without an `active` field (e.g., plugin predates this change)
- **THEN** the glasses-app SHALL leave `currentSessionId` unchanged
- **AND** SHALL still send `sessions.list` per the "requests session list on connect" requirement
- **AND** SHALL still render whatever `currentSessionName` was restored from local storage (possibly empty)

### Requirement: Glasses-app handles inbound sessions frame

The glasses-app SHALL add a `sessions` case to its inbound frame dispatcher. On receiving a `sessions` frame, the glasses-app SHALL store the items list locally (for future scroll-cycling UX) and, if `frame.active` is present and differs from the current `currentSessionId`, SHALL update `currentSessionId` and re-render the session container.

#### Scenario: sessions frame with active matching current state
- **WHEN** the glasses-app receives a `sessions` frame with `active="s1"` and the current `currentSessionId` is already `"s1"`
- **THEN** the glasses-app SHALL store the items list
- **AND** SHALL NOT trigger a re-render (no state change)

#### Scenario: sessions frame with active differing from current state
- **WHEN** the glasses-app receives a `sessions` frame with `active="s2"` and the current `currentSessionId` is `"s1"`
- **THEN** the glasses-app SHALL update `currentSessionId` to `"s2"`
- **AND** SHALL update `currentSessionName` from the matching item's `name` if present
- **AND** SHALL call `renderSession()` and `scheduleSave()`
