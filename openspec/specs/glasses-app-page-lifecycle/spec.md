# glasses-app-page-lifecycle

## Purpose

Defines the page-container lifecycle invariants for the glasses-app: which Even Hub SDK render API is called when, the one-shot-per-session contract for `createStartUpPageContainer`, the prohibition against destructive `rebuildPageContainer` from init code paths, and the minimum-content rule for `textContainerUpgrade`. Implements the canonical pattern used by every public Even Hub reference app to keep the glasses display stable across settings changes, unexpected WebView reloads, and normal app lifecycle events.

## Requirements

### Requirement: `createStartUpPageContainer` called at most once per app session

The glasses-app SHALL call `bridge.createStartUpPageContainer(...)` at most once per WebView session. A module-level `startupRendered` flag (initially `false`) SHALL gate the call: when `false`, `buildPage()` SHALL attempt the call; when `true`, `buildPage()` SHALL return immediately without invoking the SDK. The flag SHALL be set to `true` after the first call returns, regardless of whether the SDK returned `StartUpPageCreateResult.success` or a non-success code.

Rationale: the Even Hub SDK `.d.ts` documents `createStartUpPageContainer` as *"must be called when starting custom APP, subsequently use rebuildPageContainer to rebuild the page"* — i.e., one-shot per app session. Every public Even Hub reference app (`BxNxM/even-dev/apps/{clock,timer,restapi,quicktest}`, `nickustinov/paddle-even-g2`, `even-realities/evenhub-templates`, `elizaOS/eliza`, `fabioglimb/even-toolkit`) implements this exact flag pattern.

#### Scenario: First init creates the page
- **WHEN** `buildPage()` runs and `startupRendered === false`
- **THEN** the app SHALL call `bridge.createStartUpPageContainer(new CreateStartUpPageContainer(containers))`
- **AND** SHALL set `startupRendered = true` after the call returns
- **AND** SHALL log the SDK result code at INFO level

#### Scenario: Second init in the same session is a no-op
- **WHEN** `buildPage()` runs and `startupRendered === true`
- **THEN** the app SHALL return immediately without calling any `bridge.*` method
- **AND** SHALL NOT call `bridge.createStartUpPageContainer` or `bridge.rebuildPageContainer`

#### Scenario: First-call non-success is logged but does not trigger a rebuild
- **WHEN** `bridge.createStartUpPageContainer(...)` returns a non-success `StartUpPageCreateResult` (1=`invalid`, 2=`oversize`, or 3=`outOfMemory`)
- **THEN** the app SHALL log the numeric result code at INFO level
- **AND** SHALL set `startupRendered = true`
- **AND** SHALL NOT call `bridge.rebuildPageContainer` or any other destructive API
- **AND** SHALL continue to operate via subsequent `textContainerUpgrade` calls against the prior containers still held by the native layer

### Requirement: `rebuildPageContainer` SHALL NOT be called from init code paths

The glasses-app SHALL NOT call `bridge.rebuildPageContainer(...)` from `buildPage()`, `init()`, or any code path reachable from `init()`. `rebuildPageContainer` SHALL only be invoked from explicit layout-change handlers (none exist today; future handlers must be added under a separate requirement).

Rationale: Even's official documentation describes `rebuildPageContainer` as *"Replace the entire page. Full redraw — all state is lost, brief flicker on hardware"*. Commit `0c4d0a6` in this repo's history proved on real hardware that calling `rebuildPageContainer` from init tears down containers without redrawing them — "the glasses went blank." The destructive fallback was added in commit `568252f` and is the immediate cause of the user-visible "nukes everything" symptom.

#### Scenario: Init never invokes rebuildPageContainer
- **WHEN** `init()` runs (either at app startup or after a settings-save)
- **AND** `buildPage()` is called
- **THEN** `bridge.rebuildPageContainer` SHALL NOT be invoked at any point during init or buildPage execution
- **AND** no fallback chain that includes `rebuildPageContainer` SHALL exist in the init code path

#### Scenario: createStartUpPageContainer non-success does not trigger rebuild
- **WHEN** `bridge.createStartUpPageContainer(...)` returns non-success during init
- **THEN** the app SHALL log the result and continue without calling `rebuildPageContainer`
- **AND** the app SHALL remain functional via `textContainerUpgrade` against the native layer's prior containers

### Requirement: Settings-save SHALL NOT call `location.reload()`

The settings-save click handler SHALL persist the new bridge URL and token to `localStorage`, close the config overlay, close any existing WebSocket, reset reconnect state, refresh the assistant/status/session containers via `textContainerUpgrade`, and re-open the WebSocket via `connect()`. The handler SHALL NOT call `location.reload()`, `location.href = ...`, `history.go(0)`, or any other API that triggers a WebView navigation event.

Rationale: `location.reload()` was the root cause of the `startupRendered` flag being reset in commit `be85272`. Every public Even Hub reference app avoids full-page reloads; the canonical pattern assumes `init()` runs exactly once per WebView session. Removing reload aligns our app with that assumption.

#### Scenario: Settings-save with valid inputs reconnects without reload
- **WHEN** the user enters a non-empty `wss://`-prefixed URL and a non-empty token
- **AND** clicks the Save button
- **THEN** the app SHALL write the values to `localStorage` under keys `bridge_url` and `bridge_token`
- **AND** SHALL remove the config overlay from the DOM
- **AND** SHALL close any open WebSocket (`ws.close()` if `ws` is non-null)
- **AND** SHALL reset `reconnectAttempts` to 0 and `authFailed` to false
- **AND** SHALL call `setStatus('Connecting...')`, `renderAssistant()`, `renderSession()` to refresh the visible containers
- **AND** SHALL call `connect()` to open a new WebSocket with the new URL
- **AND** SHALL NOT call `location.reload()` or any navigation-triggering API

#### Scenario: Settings-save with invalid inputs shows an inline error and does not reload
- **WHEN** the user submits an empty URL, empty token, or URL without `ws://`/`wss://` prefix
- **THEN** the app SHALL show an inline error in the existing `#hermes-error` element
- **AND** SHALL NOT call `location.reload()` or modify `localStorage`
- **AND** SHALL NOT close the WebSocket or reset connection state

#### Scenario: Settings-save preserves the startupRendered flag
- **WHEN** the user saves new settings after the app has already initialized
- **THEN** the `startupRendered` flag SHALL remain `true` after the save handler completes
- **AND** no `bridge.createStartUpPageContainer` call SHALL occur as a result of the save
- **AND** no `bridge.rebuildPageContainer` call SHALL occur as a result of the save

### Requirement: Empty content SHALL be guarded to a single space before sending to `textContainerUpgrade`

Any code path that calls `textContainerUpgrade` (directly or via the `setStatus`, `renderAssistant`, or `renderSession` helpers) SHALL ensure the `content` field is at minimum a single space character (`' '`). Empty strings SHALL be replaced with `' '` before being passed to the SDK.

Rationale: the `glasses-ui` skill documents the rule *"single space — required, cannot be empty"*. Empty content is silently rejected on real hardware, causing the prior content to remain visible — producing stale UI state.

#### Scenario: setStatus called with empty string sends a single space
- **WHEN** `setStatus('')` is invoked (e.g., from `handleToolEnd`)
- **THEN** the app SHALL pass `' '` (single space) as the `content` field to `TextContainerUpgrade`
- **AND** the SDK SHALL receive a non-empty string

#### Scenario: setStatus called with non-empty string passes the string through unchanged
- **WHEN** `setStatus('Connected')` is invoked
- **THEN** the app SHALL pass `'Connected'` as the `content` field to `TextContainerUpgrade`
- **AND** no transformation SHALL be applied
