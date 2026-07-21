## 1. Bridge-call resilience wrappers (glasses-app/src/main.ts)

- [x] 1.1 Add `BRIDGE_TIMEOUT_MS = 4000` constant near the existing configuration block (after `getConfig`/`isConfigured`).
- [x] 1.2 Add a `BridgeTimeoutError` class (extends `Error`) and a `withBridgeTimeout<T>(p: Promise<T>, ms: number = BRIDGE_TIMEOUT_MS): Promise<T>` helper that races `p` against a timer that rejects with `BridgeTimeoutError`. On rejection, the helper logs `[Hermes] bridge call timed out after ${ms}ms` and re-throws so the caller's existing `try/catch` runs.
- [x] 1.3 Add `let bridgeChain: Promise<unknown> = Promise.resolve();` at module scope and a `serializedBridge<T>(p: () => Promise<T>): Promise<T>` helper that appends `p` to the chain via `.then(p, p)` and updates `bridgeChain = result.catch(() => undefined)` so failures don't poison the chain. Returns the per-call result promise.
- [x] 1.4 Add a combined helper `runBridge<T>(label: string, p: () => Promise<T>): Promise<T>` that wraps `serializedBridge` + `withBridgeTimeout` + the existing `try { ... } catch (e) { console.warn('[Hermes] <label> failed:', e) }` pattern. Callers pass a thunk (not a started promise) so serialization controls when work starts. Returns `Promise<T | undefined>` on error.

## 2. Route every bridge.* call through the wrappers

- [x] 2.1 `upgradeText(cid, cname, content)` → route the `bridge.textContainerUpgrade(...)` call through `runBridge('textContainerUpgrade', ...)`. Keep the existing behavior of swallowing errors silently (the wrapper already does this).
- [x] 2.2 `restoreState()` → route the `bridge.getLocalStorage(STATE_KEY)` call through `runBridge('getLocalStorage', ...)`.
- [x] 2.3 `saveState()` → route the `bridge.setLocalStorage(...)` call through `runBridge('setLocalStorage', ...)`.
- [x] 2.4 `toggleMic()` → route both `bridge.audioControl(true, AudioInputSource.Glasses)` and `bridge.audioControl(false)` through `runBridge('audioControl', ...)`. Preserve the existing logic that toggles `isCapturing` and sets status based on success/failure — failure to start now means the wrapper returned `undefined`, not that the call returned `false`.
- [x] 2.5 `maybeBringToFront()` → route `bridge.callEvenApp('bringToFront')` through `runBridge('callEvenApp', ...)`.
- [x] 2.6 `buildPage()` → route `bridge.createStartUpPageContainer(...)` through `runBridge('createStartUpPageContainer', ...)`. Keep the existing `StartUpPageCreateResult` logging on success.
- [x] 2.7 Double-tap handler → route `bridge.shutDownPageContainer(1)` through `runBridge('shutDownPageContainer', ...)` (fire-and-forget; the system dialog handles UX after this).

## 3. Event-listener unsubscribe + idempotent cleanup

- [x] 3.1 Add `let unsubscribeEvents: (() => void) | null = null;` at module scope.
- [x] 3.2 In `registerEventHandler()`, capture the return value: `unsubscribeEvents = bridge.onEvenHubEvent((event) => { ... });`.
- [x] 3.3 Add `let cleanupDone = false;` at module scope.
- [x] 3.4 Rewrite `cleanupAndExit()` to: (1) return early if `cleanupDone` is true, (2) set `cleanupDone = true`, (3) `try { unsubscribeEvents?.(); } catch (e) { console.warn('[Hermes] unsubscribe failed:', e); } unsubscribeEvents = null;`, (4) if `isCapturing && bridge`, `void runBridge('audioControl', () => bridge!.audioControl(false))`, (5) if `ws`, `ws.close(); ws = null;`, (6) `void saveState()`.
- [x] 3.5 Add an inline comment on the `DOUBLE_CLICK_EVENT` case linking to the `handle-input` skill and explaining why cleanup is deferred to `SYSTEM_EXIT_EVENT`/`ABNORMAL_EXIT_EVENT`.

## 4. Status container border removal

- [x] 4.1 In `buildPage()`, change the `status` container's `TextContainerProperty` from `borderWidth: 1, borderColor: 8` to `borderWidth: 0, borderColor: 0`. Leave position (`xPosition`, `yPosition`, `width`, `height`), `paddingLength`, `containerID`, `containerName`, `isEventCapture`, and `content` unchanged.

## 5. README touch-handler table sync

- [x] 5.1 In `/home/doni/projects/even-g2-hermes-bridge/README.md`, update the "touch handlers" sentence in the `glasses-app/` architecture bullet to: `tap=toggle mic, double-tap=exit dialog (system confirmation), scroll=switch session`. Verify there are no other places in the repo that mention "double-tap=interrupt" (`grep -rn "double-tap" .` should confirm only the README needed updating).

## 6. Tests (glasses-app/tests/)

- [x] 6.1 Create `glasses-app/tests/bridge.test.ts` with Vitest tests for `withBridgeTimeout`: (a) resolves with the underlying value when the promise settles before `ms`, (b) rejects with `BridgeTimeoutError` when the promise settles after `ms`, (c) the timeout timer is cleared when the underlying promise settles first (use `vi.useFakeTimers()` and `vi.advanceTimersByTime`).
- [x] 6.2 In the same file, add tests for `serializedBridge`: (a) two calls run in declaration order (second starts only after first settles), (b) a rejected first call does not block the second call, (c) the chain remains usable after multiple failures. Use deterministic fake promises controlled by `new Promise(resolve => ...)` and `vi.runAllTimersAsync()`.
- [x] 6.3 In the same file, add tests for `runBridge`: (a) returns the underlying value on success, (b) returns `undefined` and logs a warning on timeout (spy on `console.warn`), (c) returns `undefined` and logs a warning on synchronous throw.
- [x] 6.4 Helpers live in `src/lib/bridge.ts` as a `createBridgeQueue()` factory (deviation from the original "inline in main.ts" plan, captured in design.md D1 — testability requires avoiding `main.ts` import which would hang on `waitForEvenAppBridge`). `main.ts` imports the factory and instantiates one module-scope queue. Tests create fresh queues per test. No `vitest.config.ts` change needed (existing `tests/**/*.test.ts` glob picks up the new file).
- [x] 6.5 Optional but recommended: integration-style test for `cleanupAndExit` idempotency — **deferred**. `cleanupAndExit` lives in `main.ts` which can't be imported without hanging on `waitForEvenAppBridge()`; extracting it just for one test would over-engineer the module. The function's idempotency rests on a trivially-correct `cleanupDone` boolean guard (verified by inspection). All underlying primitives (`runBridge`, `serializedBridge`, `withBridgeTimeout`) are unit-tested in `bridge.test.ts`. A follow-up change can add integration coverage if a real idempotency regression ever appears.

## 7. Verify

- [x] 7.1 `cd glasses-app && npm run typecheck` — must pass with zero errors.
- [x] 7.2 `cd glasses-app && npm run lint` — must pass (ESLint 9 flat config, type-checked rules). Fix any new lint findings introduced by the wrapper code (likely candidates: explicit return types on generic helpers, `no-unused-vars` on the `BridgeTimeoutError` constructor if tree-shaken — both fixable with explicit annotations).
- [x] 7.3 `cd glasses-app && npm run test` — all existing tests (`frames`, `reconnect`, `session`, `state`) and the new `bridge.test.ts` must pass.
- [x] 7.4 `cd glasses-app && npm run build` — Vite production build must succeed (catches any `tsc`-invisible import cycles the new exports might create).
- [x] 7.5 `grep -rn "double-tap=interrupt" /home/doni/projects/even-g2-hermes-bridge/` — returns 3 matches, all inside this change's own openspec docs (proposal/design/tasks) which intentionally describe the gap that was fixed. User-facing docs (README.md) return zero matches.
- [x] 7.6 `grep -n "borderWidth: 1, borderColor: 8" /home/doni/projects/even-g2-hermes-bridge/glasses-app/src/main.ts` — returns zero matches after the border removal.
- [x] 7.7 Sanity-read the final `main.ts` end-to-end and confirm: (a) every `bridge.*` call is inside `runBridge`, (b) `unsubscribeEvents` is captured at registration and called in `cleanupAndExit`, (c) `cleanupDone` guards `cleanupAndExit`, (d) the `DOUBLE_CLICK_EVENT` case has the explanatory comment and calls `shutDownPageContainer(1)` only.

## 8. OpenSpec wrap

- [ ] 8.1 Run `openspec validate align-glasses-app-best-practices` and fix any reported spec/task issues.
- [ ] 8.2 Stage and commit atomically: `feat(glasses-app): align with Even G2 best practices (exit dialog, timeouts, serialization)` plus the README touch in the same commit (or split as `docs(repo): sync touch handlers with glasses-app` if the reviewer prefers per-directory commits per `AGENTS.md`). Pair the new test file with the implementation in the same commit per the "Pair tests with implementation" git convention.
