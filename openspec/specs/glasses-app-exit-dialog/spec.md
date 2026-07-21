# glasses-app-exit-dialog

## Purpose

Defines the user-facing exit interaction for the glasses-app: how the user triggers an exit, what the SDK does in response, and how the app cleans up safely when the exit is confirmed or forced. Implements the canonical `handle-input` skill pattern for Even Hub G2 apps.

## Requirements

### Requirement: Exit dialog via system confirmation on double-tap

The glasses-app SHALL use the SDK's built-in system exit confirmation dialog (via `bridge.shutDownPageContainer(1)`) as the only exit interaction. No custom in-canvas "Are you sure?" UI SHALL be built. The double-tap (`DOUBLE_CLICK_EVENT`, `OsEventTypeList` value `3`) SHALL be the only gesture that triggers the exit dialog.

Rationale: the `handle-input` skill says "Use the SDK's built-in system exit dialog rather than building your own confirmation UI" and prescribes the `DOUBLE_CLICK_EVENT` → `shutDownPageContainer(1)` pattern.

#### Scenario: Double-tap opens the system exit dialog
- **WHEN** a `DOUBLE_CLICK_EVENT` (`OsEventTypeList` value `3`) arrives while the app is in the foreground
- **THEN** the app SHALL call `bridge.shutDownPageContainer(1)` exactly once
- **AND** the app SHALL NOT release hardware, close the WebSocket, unsubscribe the event listener, or flush state at this point (the user may still cancel)

#### Scenario: Single-tap is not interpreted as exit
- **WHEN** a single press (`OsEventTypeList.CLICK_EVENT`, value `0`) arrives
- **THEN** the app SHALL toggle the microphone capture (existing behavior)
- **AND** SHALL NOT trigger the exit dialog

#### Scenario: Scroll is not interpreted as exit
- **WHEN** a `SCROLL_TOP_EVENT` (value `1`) or `SCROLL_BOTTOM_EVENT` (value `2`) arrives
- **THEN** the app SHALL switch the active session (existing behavior)
- **AND** SHALL NOT trigger the exit dialog

### Requirement: Cleanup runs exactly once on system or abnormal exit

The app SHALL perform all teardown work (unsubscribe the event listener, release the microphone, close the WebSocket, flush state to `setLocalStorage`) in response to `SYSTEM_EXIT_EVENT` (`OsEventTypeList` value `7`) or `ABNORMAL_EXIT_EVENT` (`OsEventTypeList` value `6`) — never in response to the double-tap itself. The teardown function SHALL be idempotent: if the SDK fires both events for the same teardown, the second invocation SHALL be a no-op.

Rationale: the `handle-input` skill says "Do not `unsubscribe()` / stop hardware / flush state *before* calling `shutDownPageContainer(1)`. If you do and the user taps cancel, the app is still on screen but no longer listening for events. Clean up in the `ABNORMAL_EXIT_EVENT` / `SYSTEM_EXIT_EVENT` handlers instead."

#### Scenario: User confirms the exit dialog
- **WHEN** the system exit dialog is shown and the user confirms
- **THEN** the SDK fires `SYSTEM_EXIT_EVENT` (`OsEventTypeList` value `7`)
- **AND** the app SHALL run the cleanup function exactly once

#### Scenario: User cancels the exit dialog
- **WHEN** the system exit dialog is shown and the user cancels
- **THEN** no `SYSTEM_EXIT_EVENT` or `ABNORMAL_EXIT_EVENT` is fired
- **AND** the app SHALL NOT have run any cleanup
- **AND** the microphone, WebSocket, event listener, and rendered state SHALL remain intact and functional

#### Scenario: System forces an abnormal exit
- **WHEN** the SDK fires `ABNORMAL_EXIT_EVENT` (`OsEventTypeList` value `6`) without a prior `SYSTEM_EXIT_EVENT`
- **THEN** the app SHALL run the cleanup function exactly once

#### Scenario: Both exit events fire for the same teardown
- **WHEN** `ABNORMAL_EXIT_EVENT` fires followed by `SYSTEM_EXIT_EVENT` (or vice versa) within the same page lifetime
- **THEN** the cleanup function SHALL run exactly once (on the first event)
- **AND** the second invocation SHALL be a no-op (no second `ws.close()`, no second `unsubscribeEvents()`, no second `saveState()`)
- **AND** no JavaScript error SHALL be raised by the second invocation

### Requirement: Event listener unsubscribed on teardown

The app SHALL capture the unsubscribe function returned by `bridge.onEvenHubEvent(...)` at registration time and SHALL invoke it during the teardown function. The unsubscribe SHALL happen before the WebSocket is closed and before `saveState()` is called, so the listener cannot fire into a tearing-down app.

Rationale: the `handle-input` skill says "Always call [the unsubscribe function] on component teardown."

#### Scenario: Unsubscribe is captured and called
- **WHEN** `cleanupAndExit()` runs
- **THEN** the captured unsubscribe function SHALL be invoked
- **AND** the WebSocket SHALL be closed after the unsubscribe returns
- **AND** `saveState()` SHALL be flushed after the unsubscribe returns

#### Scenario: Unsubscribe failure does not abort cleanup
- **WHEN** the captured unsubscribe function throws
- **THEN** the app SHALL swallow the error and continue with the rest of the teardown (close WebSocket, flush state)
- **AND** SHALL log a warning to the JS console

### Requirement: README documents the exit interaction accurately

The root `README.md` SHALL list the glasses-app touch handlers as `tap=toggle mic, double-tap=exit dialog (system confirmation), scroll=switch session`. The README SHALL NOT describe the double-tap as "interrupt" or any behavior other than triggering the system exit dialog.

#### Scenario: Reader of the README sees the correct double-tap behavior
- **WHEN** a contributor reads the touch-handler table in `README.md`
- **THEN** the table SHALL say `double-tap=exit dialog (system confirmation)`
- **AND** SHALL match what `glasses-app/src/main.ts` actually does on `DOUBLE_CLICK_EVENT`
