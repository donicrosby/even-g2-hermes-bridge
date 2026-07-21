## Why

The glasses-app already wires `shutDownPageContainer(1)` on double-tap (so the system exit dialog does fire), but the surrounding code violates several Even Realities G2 best practices from the official skills (`handle-input`, `glasses-ui`, `design-guidelines`): the `onEvenHubEvent` subscription is never unsubscribed, BLE/bridge calls have no per-call timeout, render calls are not serialized, the README documents the wrong double-tap behavior, and the in-canvas status bar carries a decorative border that contradicts the design system. These gaps make the app fragile on flaky BLE links and make the exit path harder to discover/verify. This change closes those gaps so the app behaves correctly under real hardware conditions and matches the Even Hub conventions that the SDK and design skills prescribe.

## What Changes

- **Verify** the exit dialog wiring on `DOUBLE_CLICK_EVENT` is correct (call `bridge.shutDownPageContainer(1)`, defer cleanup to `SYSTEM_EXIT_EVENT`/`ABNORMAL_EXIT_EVENT`). Today's call is in the right place; this change hardens it with an explicit comment, a unit test for the dispatcher, and a guard so cleanup runs at most once.
- **Add** an `unsubscribe` path for `bridge.onEvenHubEvent()` and call it from `cleanupAndExit()` so the listener is detached before the WebSocket closes and the page tears down. Today the subscription is leaked for the lifetime of the WebView.
- **Add** a per-call timeout wrapper (`withBridgeTimeout`, default 4 s) around every `bridge.*` call that hits BLE (`textContainerUpgrade`, `audioControl`, `setLocalStorage`, `getLocalStorage`, `shutDownPageContainer`, `callEvenApp`). A single flaky hop can otherwise hang the JS event loop for ~30 s.
- **Add** a serialized-bridge-call queue (`serializedBridge<T>(p)`) so `textContainerUpgrade`, `getLocalStorage`, `setLocalStorage`, and `audioControl` never overlap. Concurrent render + storage calls can crash the BLE link per the `glasses-ui` skill.
- **Remove** the decorative border (`borderWidth: 1, borderColor: 8`) on the `status` container — design system uses borders only for selection highlight, not chrome. Keep `borderWidth: 0` on `assistant` and `session` (already correct).
- **Fix** the root `README.md` touch-handler table: it currently says `double-tap=interrupt`; the code says `double-tap=exit dialog`. Update the README to match the code (and add `tap=toggle mic`, `scroll=switch session` which were already correct but undocumented in the table).
- **Add** unit tests for the new pure helpers (`withBridgeTimeout`, `serializedBridge`, and the event dispatcher's exit-and-cleanup path) in `glasses-app/tests/` following the existing Vitest pattern. No new test for the `unsubscribe` wiring itself (it is observable only through integration), but the dispatcher test asserts the cleanup function is called once.
- **No change** to the WS protocol, the plugin, the bridge-server, or the container layout (3 containers, 576×288 canvas, same `containerID`/`containerName` assignments).

## Capabilities

### New Capabilities
- `glasses-app-exit-dialog`: Defines the user-facing exit interaction (double-tap → system exit dialog → user confirms → `SYSTEM_EXIT_EVENT` fires → cleanup runs exactly once then unsubscribes). Covers the canonical pattern from the `handle-input` skill and the cleanup-once guarantee.
- `glasses-app-bridge-resilience`: Defines the wrappers every `bridge.*` call must pass through — per-call timeout, serialized execution, and the unsubscribe-on-teardown contract. Covers BLE-link safety per the `glasses-ui` skill.

### Modified Capabilities
<!-- None — openspec/specs/ is empty (the prior change's specs have not been synced yet). The two new capabilities above will fold into `glasses-ws-app` when that change's specs land. -->

## Impact

- **Code**: ~70 lines in new `glasses-app/src/lib/bridge.ts` (factory + helpers + types), ~60–80 new lines in `glasses-app/src/main.ts` (unsubscribe capture, cleanup-once guard, border removal, comment on the exit-dialog call, queue instantiation) plus ~150–200 lines of new Vitest tests in `glasses-app/tests/bridge.test.ts`. Two one-line README fixes. The factory extraction is a small, design-documented deviation from the original "inline in main.ts" plan (see design.md D1 for the learning).
- **Dependencies**: None. Uses the existing `@evenrealities/even_hub_sdk` and Vitest setup. No npm adds.
- **Behavior**: User-visible changes are (a) the status bar no longer has a thin border line, (b) the app no longer hangs for ~30 s when a single BLE call wedges, and (c) the README now matches what the app does. The exit dialog itself already works.
- **Testing**: `npm run test`, `npm run typecheck`, `npm run lint` must all stay green. New tests run under the existing Vitest config.
- **Rollback**: Pure local revert of `glasses-app/src/main.ts`, `glasses-app/tests/`, and `README.md`. No protocol or plugin changes to coordinate.
- **Non-goals**: Rewriting the phone-side config screen, adding new touch gestures, changing the WS protocol, touching `plugin/` or `bridge-server/`, or migrating off the SDK 0.0.12 `setLocalStorage` fallback for background state. Those are out of scope and would each deserve their own change.
