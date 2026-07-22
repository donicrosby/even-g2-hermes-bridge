## Why

Bridge credentials (URL + token) are stored in browser `localStorage`, which is destroyed when the Even Hub host app closes the WebView. The app state (assistant text, sessions) is stored in SDK `bridge.setLocalStorage` which survives WebView destruction. This mismatch means every time the user closes and reopens the glasses-app, the credentials are gone and the app shows the config screen — the user has to re-enter URL + token every time.

## What Changes

- **Add** `bridgeUrl: string` and `bridgeToken: string` to the `GlassesAppState` snapshot in `glasses-app/src/lib/state.ts`. These get serialized/deserialized alongside the existing assistant text + session data via the same `bridge.setLocalStorage(STATE_KEY)` path.
- **Change** `getConfig()` from synchronous browser-`localStorage` reads to reading from the restored state (populated by `restoreState()` which reads via `bridge.getLocalStorage`).
- **Change** the settings-save handler from `localStorage.setItem(...)` to updating the state variables + calling `saveState()` (which writes via `bridge.setLocalStorage`).
- **Reorder** the init flow so `restoreState()` runs before `isConfigured()` / `connect()`, ensuring credentials are available before the connect decision.
- **Remove** all remaining browser `localStorage` reads/writes for bridge credentials.

## Capabilities

### Modified Capabilities
- `glasses-app-page-lifecycle`: add a requirement that bridge credentials SHALL be persisted via SDK storage, not browser localStorage.

## Impact

**Affected code:**
- `glasses-app/src/lib/state.ts` — add two fields to `GlassesAppState`, `serializeState`, `mergeState`
- `glasses-app/src/main.ts` — `getConfig()` reads from state, settings-save writes to state, init flow reordered
- `glasses-app/tests/state.test.ts` — add tests for the new fields

**No protocol changes, no plugin changes, no server changes.**

**Runtime behavior change:** bridge credentials survive app close/reopen. User enters them once; they persist until explicitly changed.
