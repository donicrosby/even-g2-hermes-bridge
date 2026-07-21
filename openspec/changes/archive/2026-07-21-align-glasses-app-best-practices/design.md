## Context

The glasses-app ships today with three text containers on the 576×288 canvas, a double-tap-to-exit handler that calls `bridge.shutDownPageContainer(1)`, and a cleanup path on `ABNORMAL_EXIT_EVENT`/`SYSTEM_EXIT_EVENT`. A close read against the three official Even skills (`handle-input`, `glasses-ui`, `design-guidelines`) surfaced five concrete gaps:

1. `bridge.onEvenHubEvent(...)` returns an unsubscribe function that the app never captures or calls. The listener stays attached for the lifetime of the WebView.
2. Every `bridge.*` call (`textContainerUpgrade`, `audioControl`, `setLocalStorage`, `getLocalStorage`, `shutDownPageContainer`, `callEvenApp`) is fired and forgotten. A single wedged BLE hop hangs the JS event loop for ~30 s (per the `glasses-ui` skill's "BLE calls can hang" note).
3. Multiple `textContainerUpgrade` calls can overlap when `assistant.delta` frames arrive in a burst. The `glasses-ui` skill says concurrent render + storage calls can crash the connection.
4. The `status` container declares `borderWidth: 1, borderColor: 8`. The `design-guidelines` skill reserves borders for selection highlight, not chrome.
5. The root `README.md` touch-handler table says `double-tap=interrupt`; the code says `double-tap=exit dialog`. New contributors will be misled.

The exit dialog itself is already correctly wired; this change hardens the surrounding code.

## Goals / Non-Goals

**Goals:**
- Capture and call the `onEvenHubEvent` unsubscribe function on teardown so the listener detaches before the WebSocket closes.
- Wrap every `bridge.*` call in a per-call timeout (default 4 s) so a flaky BLE hop can't hang the app.
- Serialize `bridge.*` calls so no two overlap, eliminating the concurrent-call crash risk.
- Guarantee `cleanupAndExit` runs at most once per page lifetime (idempotent), so a double-fire of `ABNORMAL_EXIT_EVENT` followed by `SYSTEM_EXIT_EVENT` doesn't double-close the WebSocket or double-save state.
- Remove the decorative border on the `status` container to match the design system.
- Sync the README touch-handler table with the code.

**Non-Goals:**
- Rewriting the phone-side config screen (still inline-styled — works fine, separate concern).
- Migrating off the SDK 0.0.12 `setLocalStorage`/`getLocalStorage` fallback for background state (still required until the SDK ships `setBackgroundState`/`onBackgroundRestore`).
- Changing the WS protocol, the plugin, or the bridge-server.
- Adding new touch gestures or new containers.
- Adding image containers or a custom in-canvas exit confirmation (the SDK's `shutDownPageContainer(1)` system dialog is the canonical pattern per `handle-input`).

## Decisions

### D1: Extract wrappers to `src/lib/bridge.ts` as a factory (revised from inline-in-main.ts)

**Choice.** Extract `withBridgeTimeout`, `serializedBridge`, `runBridge`, and `BridgeTimeoutError` to a new `src/lib/bridge.ts` file, exposed via a `createBridgeQueue(): BridgeQueue` factory. `main.ts` imports the factory, instantiates one queue at module scope (`const queue = createBridgeQueue()`), and aliases `const runBridge = queue.runBridge` so call sites stay terse. Tests import the factory and create fresh queues per test.

**Rationale.** The original plan was inline-in-`main.ts`, but that makes the helpers untestable without importing `main.ts` — which triggers `init()` → `waitForEvenAppBridge()` (hangs forever in Node) and `localStorage.getItem()` (throws in Node). The factory pattern keeps the lib file genuinely pure (no module-level state — the chain lives in the factory closure) so it matches the existing `src/lib/` convention (`frames.ts`, `reconnect.ts`, `session.ts`, `state.ts` are all pure), and gives each test a fresh queue via `createBridgeQueue()` so serialized-chain state never leaks across tests.

**Alternative considered (rejected).** Keep helpers inline in `main.ts` and use `import.meta.env.MODE === 'test'` to guard the `init()` call. Rejected: it doesn't solve the `localStorage` ReferenceError, doesn't help future tests that want a fresh queue, and pollutes `main.ts` with test-aware branching.

**Alternative considered (rejected).** Pass the chain as an explicit argument instead of closing over it. Rejected: every call site would gain a `queue` parameter, threading state through the call graph for no real benefit over a factory closure.

### D2: Per-call timeout via `Promise.race`, default 4 s, configurable via constant

**Choice.** `withBridgeTimeout<T>(p: Promise<T>, ms = BRIDGE_TIMEOUT_MS = 4000): Promise<T>` races `p` against a timer that rejects with `BridgeTimeoutError`. The wrapper logs a warning and swallows on timeout (matching the existing `try { ... } catch (e) { console.warn(...) }` pattern in `restoreState`/`saveState`/`upgradeText`).

**Rationale.** The `glasses-ui` skill's "per-call timeout to BLE calls" guidance explicitly suggests `Promise.race` with "a few-second cap". 4 s is conservative — normal BLE hops land in 50–500 ms, image sends 0.5–2 s, so 4 s catches only the wedged cases. The constant lives at the top of `main.ts` next to the other configuration.

**Alternative considered.** Use `AbortController` + `signal`. Rejected: the SDK methods don't accept an `AbortSignal`, so aborting the wrapper doesn't actually cancel the underlying call — the timer trick has the same observable behavior with less plumbing.

### D3: Serialize via a single promise-chain tail

**Choice.** `let bridgeChain: Promise<unknown> = Promise.resolve();` at module scope. `serializedBridge<T>(p: () => Promise<T>): Promise<T>` appends `p` to the chain and returns the result. Callers pass a thunk (not a pre-started promise) so we control when the work actually starts.

```ts
let bridgeChain: Promise<unknown> = Promise.resolve();
function serializedBridge<T>(p: () => Promise<T>): Promise<T> {
  const result = bridgeChain.then(p, p) as Promise<T>;
  bridgeChain = result.catch(() => undefined);
  return result;
}
```

The `.then(p, p)` runs `p` whether the previous step resolved or rejected — failures don't wedge the queue. The `bridgeChain = result.catch(() => undefined)` makes the chain never reject, so a single failure can't poison every subsequent call.

**Rationale.** The `glasses-ui` skill is explicit: "Serialize all bridge calls, not just images — `await` each before starting the next; concurrent render + storage calls can crash the connection." A single chain is the simplest implementation that satisfies that.

**Alternative considered.** A debouncing queue with per-method keys (so two `textContainerUpgrade` calls coalesce but a `getLocalStorage` runs in parallel). Rejected: it's strictly more complex than the skill calls for, and the skill's wording is "serialize all bridge calls" — not "serialize per-method".

**Alternative considered.** Use the existing `scheduleSave` debounce to also coalesce `textContainerUpgrade`. Rejected: that would drop intermediate renders during a streaming `assistant.delta` burst, which is a behavior change the user can see (text would jump instead of stream).

### D4: Capture unsubscribe, call it exactly once in `cleanupAndExit`

**Choice.** At registration time:

```ts
let unsubscribeEvents: (() => void) | null = null;
// in registerEventHandler():
unsubscribeEvents = bridge.onEvenHubEvent((event) => { ... });
```

In `cleanupAndExit()`:

```ts`
let cleanupDone = false;
function cleanupAndExit(): void {
  if (cleanupDone) return;
  cleanupDone = true;
  if (unsubscribeEvents) { try { unsubscribeEvents(); } catch {} unsubscribeEvents = null; }
  if (isCapturing && bridge) { void bridge.audioControl(false); }
  if (ws) { ws.close(); ws = null; }
  void saveState();
}
```

**Rationale.** The `handle-input` skill explicitly says: "Always clean up on teardown — `bridge.onEvenHubEvent()` returns an unsubscribe function. Always call it on component teardown." The `cleanupDone` guard exists because the SDK can fire both `ABNORMAL_EXIT_EVENT` and `SYSTEM_EXIT_EVENT` for the same teardown, and double-unsubscribe / double-close is observable (throws in the SDK; logs a warning on `ws.close()` after the first close).

**Alternative considered.** Trust the SDK to deduplicate. Rejected: the skill's own template calls cleanup in both handlers; without a guard, the app relies on undocumented SDK behavior.

### D5: Remove the status-container border, keep the layout pixel-identical

**Choice.** Change `borderWidth: 1, borderColor: 8` to `borderWidth: 0, borderColor: 0` on the `status` container in `buildPage()`. Leave position, size, and `paddingLength` untouched.

**Rationale.** The `design-guidelines` skill says borders are for selection highlight (`Toggle borderWidth on individual text containers between 0 (unselected) and a nonzero value (selected)`). A persistent border on a status bar is decorative chrome, which the design system doesn't use. Removing it costs nothing — the text content still renders identically inside the padded rect.

**Alternative considered.** Replace the border with a single `─` row of Unicode box-drawing characters as a divider. Rejected: that's still decorative chrome, just done with text — same design-system violation, more code.

### D6: README touch-handler table sync (no architecture choice, just truth)

**Choice.** Update the README's "touch handlers" line to:

> tap=toggle mic, double-tap=exit dialog (system confirmation), scroll=switch session

**Rationale.** The code already does this. The README is simply out of date.

## Risks / Trade-offs

- **[Per-call timeout hides real failures]** If a `textContainerUpgrade` legitimately takes >4 s (e.g., a giant render queued behind an image), the wrapper will log and drop it. → *Mitigation*: 4 s is well above the observed p99 for non-image calls; image calls aren't routed through `textContainerUpgrade`. If we ever see the warning in logs, we bump the constant or special-case the slow call.
- **[Serialized chain lowers render throughput]** Streaming `assistant.delta` frames now queue behind each other; a slow render can delay the next. → *Mitigation*: renders are ~50–100 ms each, faster than the WS frame inter-arrival time in practice. If throughput ever becomes visible, we coalesce via the existing `scheduleSave`-style debounce — but only as a follow-up, since it changes user-visible streaming behavior.
- **[`cleanupDone` swallows second teardown]** If `SYSTEM_EXIT_EVENT` fires and `cleanupAndExit` already ran from a prior `ABNORMAL_EXIT_EVENT`, the second call is a no-op. → *Mitigation*: this is correct behavior — both events signal the same teardown. The guard prevents double-close, which is the actual hazard.
- **[README change might confuse existing BYOA users]** Anyone who memorized "double-tap=interrupt" sees a behavior change documented. → *Mitigation*: the *code* already does exit-on-double-tap; only the docs were wrong. Anyone testing the actual app already sees the exit dialog.

## Migration Plan

1. Land the change in a single PR (atomic — README + code + tests together).
2. CI gates: `npm run typecheck`, `npm run lint`, `npm run test` must all stay green.
3. No on-device migration — the WS protocol and plugin are untouched.
4. Rollback: pure `git revert`. No data format changes, no persisted-state shape changes.

## Open Questions

None. All decisions above are made; the rest is implementation detail captured in `tasks.md`.
