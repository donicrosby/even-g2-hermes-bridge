## MODIFIED Requirements

### Requirement: Bridge credentials SHALL persist via SDK storage, not browser localStorage

Bridge URL and token SHALL be stored in `GlassesAppState` (serialized via `bridge.setLocalStorage(STATE_KEY)`), NOT in browser `localStorage`. The settings-save handler SHALL update the state variables and call `saveState()` (which writes via BLE to the phone-side Even Hub app). The `getConfig()` function SHALL read from the restored state variables, not from `localStorage.getItem(...)`.

Rationale: Even Hub destroys the WebView on app close, wiping browser `localStorage`. SDK storage persists because it writes via BLE to the phone-side Even Hub app. The app state already uses this pattern successfully; credentials must follow the same path.

#### Scenario: User enters credentials, closes app, reopens
- **WHEN** the user enters bridge URL + token and clicks Save
- **THEN** the credentials SHALL be written via `bridge.setLocalStorage(STATE_KEY, ...)` as part of the state snapshot
- **AND** when the user closes and reopens the app, `restoreState()` SHALL read the credentials back from SDK storage
- **AND** `isConfigured()` SHALL return true
- **AND** the app SHALL skip the config screen and connect directly

#### Scenario: First run with no stored credentials
- **WHEN** the app starts for the first time and no SDK storage exists
- **THEN** `restoreState()` SHALL populate `bridgeUrl` and `bridgeToken` with empty strings (via `??` fallback)
- **AND** `isConfigured()` SHALL return false
- **AND** the config screen SHALL be shown

#### Scenario: One-time migration from browser localStorage
- **WHEN** the app starts and `localStorage.getItem('bridge_url')` returns a non-empty value AND the restored state has no `bridgeUrl`
- **THEN** the app SHALL copy the browser localStorage values into the state variables
- **AND** SHALL call `saveState()` to persist them via SDK storage
- **AND** SHALL clear the browser localStorage entries

#### Scenario: Settings-save persists via SDK storage
- **WHEN** the user enters new credentials and clicks Save
- **THEN** the handler SHALL update the `bridgeUrl` and `bridgeToken` state variables
- **AND** SHALL call `saveState()` (which writes via `bridge.setLocalStorage`)
- **AND** SHALL NOT call `localStorage.setItem(...)`
