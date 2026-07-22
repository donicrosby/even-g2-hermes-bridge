## 1. State snapshot changes

- [ ] 1.1 Add `bridgeUrl: string` and `bridgeToken: string` to `GlassesAppState` in `glasses-app/src/lib/state.ts`.
- [ ] 1.2 Add both fields to `serializeState()` output.
- [ ] 1.3 Add both fields to `mergeState()` with `??` fallback.
- [ ] 1.4 Add unit tests in `tests/state.test.ts` for the new fields (serialize, parse, merge with missing fields).

## 2. Migrate getConfig + settings-save

- [ ] 2.1 Change `getConfig()` to read from module-level state variables (`bridgeUrl`, `bridgeToken`) instead of `localStorage.getItem(...)`.
- [ ] 2.2 Add `bridgeUrl` and `bridgeToken` as module-level `let` variables in `main.ts`, initialized to `''`.
- [ ] 2.3 Update `currentMutableState()` to include `bridgeUrl` and `bridgeToken`.
- [ ] 2.4 Update `restoreState()` to set `bridgeUrl` and `bridgeToken` from the merged state.
- [ ] 2.5 Change the settings-save handler: replace `localStorage.setItem('bridge_url', ...)` and `localStorage.setItem('bridge_token', ...)` with state variable updates + `void saveState()`.

## 3. Reorder init flow

- [ ] 3.1 Move `await restoreState()` before `isConfigured()` / `connect()` in `init()`.
- [ ] 3.2 Remove the synchronous `isConfigured()` call before `restoreState()` (if any).

## 4. One-time migration from browser localStorage

- [ ] 4.1 After `restoreState()`, check if `localStorage.getItem('bridge_url')` has a value AND `bridgeUrl` is empty. If so, copy it over, call `saveState()`, and clear the localStorage entries.

## 5. Verify

- [ ] 5.1 `npm run test` — all tests pass including new state tests.
- [ ] 5.2 `npm run typecheck && npm run lint` — clean.
- [ ] 5.3 `npm run release` — build + pack succeeds.
- [ ] 5.4 Manual smoke: install fresh, enter credentials, close app, reopen — verify credentials persist.
