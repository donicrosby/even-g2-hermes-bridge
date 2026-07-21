## ADDED Requirements

### Requirement: Per-call timeout on every bridge.* call

Every call to a `bridge.*` method that traverses the BLE link (`textContainerUpgrade`, `audioControl`, `setLocalStorage`, `getLocalStorage`, `shutDownPageContainer`, `callEvenApp`, `createStartUpPageContainer`, `rebuildPageContainer`, `updateImageRawData`) SHALL be wrapped in a per-call timeout. The default timeout SHALL be 4 seconds (`BRIDGE_TIMEOUT_MS = 4000`). On timeout, the app SHALL log a warning to the JS console and swallow the error (matching the existing `console.warn('[Hermes] ... failed:', e)` pattern); the app SHALL NOT crash, throw, or set the user-visible status to an error state on a bridge timeout alone.

Rationale: the `glasses-ui` skill says "Add a per-call timeout to BLE calls — a single flaky hop can hang ~30s; wrap calls in `Promise.race` with a few-second cap."

#### Scenario: A normal bridge call completes within the timeout
- **WHEN** a `bridge.textContainerUpgrade(...)` call resolves in 80 ms
- **THEN** the wrapper SHALL return the resolved value
- **AND** no warning SHALL be logged

#### Scenario: A wedged bridge call is abandoned after 4 seconds
- **WHEN** a `bridge.textContainerUpgrade(...)` call has not settled after 4000 ms
- **THEN** the wrapper SHALL reject with a timeout error
- **AND** the calling code SHALL catch it, log a warning, and continue serving the user
- **AND** the app SHALL NOT hang the JS event loop waiting for the call

#### Scenario: A bridge call that throws synchronously is treated like a failure
- **WHEN** `bridge.textContainerUpgrade(...)` throws synchronously (e.g., the bridge handle is null)
- **THEN** the wrapper SHALL propagate the throw to the caller's existing `try/catch`
- **AND** SHALL NOT start the timeout timer

### Requirement: Serialized bridge calls

The app SHALL serialize all `bridge.*` calls so no two are in flight at the same time. Serialization SHALL be implemented as a single module-scope promise chain: each new call appends to the tail of the chain and starts only after the previous call settles (whether it resolves or rejects). A failure in one call SHALL NOT poison the chain — subsequent calls SHALL still execute.

Rationale: the `glasses-ui` skill says "Serialize all bridge calls, not just images — `await` each before starting the next; concurrent render + storage calls can crash the connection."

#### Scenario: Two rapid textContainerUpgrade calls run in sequence
- **WHEN** two `bridge.textContainerUpgrade(...)` calls are scheduled within the same tick (e.g., a burst of `assistant.delta` frames)
- **THEN** the second call SHALL start only after the first call settles
- **AND** both calls SHALL eventually execute (no call is dropped by the serialization itself)

#### Scenario: A rejected call does not block subsequent calls
- **WHEN** the first call in the chain rejects (e.g., `BridgeTimeoutError` after 4 s)
- **AND** a second call is queued behind it
- **THEN** the second call SHALL still execute
- **AND** the chain SHALL remain usable for all future calls

#### Scenario: A burst of assistant.delta frames does not crash the BLE link
- **WHEN** the server sends five `assistant.delta` frames in quick succession
- **THEN** the app SHALL produce five `textContainerUpgrade` calls
- **AND** each call SHALL run only after the previous one settles
- **AND** the BLE connection SHALL remain intact

### Requirement: Status container has no decorative border

The `status` text container (the 576×44 row at y=200 that displays connection state, tool-call labels, and transcripts) SHALL be declared with `borderWidth: 0`. No persistent border SHALL be drawn around the status container. This requirement applies to the initial `createStartUpPageContainer` call and to any future `rebuildPageContainer` call.

Rationale: the `design-guidelines` skill reserves container borders for selection highlight (`Toggle borderWidth on individual text containers between 0 (unselected) and a nonzero value (selected)`). A persistent border on a status bar is decorative chrome, which the design system does not use.

#### Scenario: Status container is created without a border
- **WHEN** `buildPage()` runs and constructs the `TextContainerProperty` for the `status` container
- **THEN** the property SHALL set `borderWidth: 0`
- **AND** SHALL set `borderColor: 0` (the value is irrelevant when `borderWidth` is 0, but it SHALL be a valid greyscale level)

#### Scenario: Status text remains visible without the border
- **WHEN** the app calls `setStatus('Connected')`
- **THEN** the text `Connected` SHALL appear in the status row
- **AND** no border line SHALL surround it
