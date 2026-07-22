## Context

The Even Hub host app uses a Headless WebView migration strategy: when the app goes to background or is closed, the WebView is destroyed and recreated on reopen. Browser `localStorage` lives inside the WebView and is wiped on destruction. SDK `bridge.setLocalStorage` writes via BLE to the phone-side Even Hub app and survives WebView destruction.

The glasses-app already uses SDK storage for app state (`GlassesAppState` via `bridge.setLocalStorage(STATE_KEY)`) — this works. But bridge credentials (`bridge_url`, `bridge_token`) use browser `localStorage` — this doesn't survive.

## Decisions

### D1: Add credentials to the existing state snapshot

**Choice.** Add `bridgeUrl` and `bridgeToken` to the existing `GlassesAppState` type. They serialize/deserialize alongside the other fields via the same `serializeState` / `mergeState` / `bridge.setLocalStorage(STATE_KEY)` path.

**Alternatives considered:**
- **Separate SDK storage key** (e.g., `"bridge_creds"`). Rejected — adds a second BLE round-trip on init and a second save path. The existing snapshot is small; two more fields are negligible.
- **Keep browser localStorage + add `setBackgroundState`**. Rejected — `setBackgroundState` only handles background→foreground transitions, not full app close→reopen. SDK storage is the correct layer.

### D2: getConfig reads from state, not localStorage

**Choice.** `getConfig()` becomes a plain function that reads from module-level state variables (populated by `restoreState()` during init). It's still synchronous — no async needed because by the time `getConfig()` is called, `restoreState()` has already run and populated the variables.

**Rationale.** The init flow reorders to: `await restoreState()` → `getConfig()` → `isConfigured()` → `connect()`. Since `restoreState()` is awaited, the state variables are populated before `getConfig()` reads them.

### D3: Settings-save updates state + calls saveState()

**Choice.** The settings-save click handler updates the `bridgeUrl` / `bridgeToken` state variables, then calls `saveState()` (which debounces + writes via `bridge.setLocalStorage`). No more `localStorage.setItem(...)`.

## Risks

- **[Risk: first-run has no stored credentials]** → *Mitigation*: `restoreState()` returns empty strings for new fields via the `??` fallback in `mergeState`. `isConfigured()` returns false, config screen shows. Same UX as today for first run.
- **[Risk: existing users with credentials in localStorage lose them on upgrade]** → *Mitigation*: one-time migration — if `localStorage.getItem('bridge_url')` returns a value AND state has no `bridgeUrl`, copy it over. This is a 3-line check in `init()`.
