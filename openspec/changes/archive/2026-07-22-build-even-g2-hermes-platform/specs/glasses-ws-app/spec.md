## ADDED Requirements

### Requirement: Glasses-app connects via WebSocket on launch

The rewritten glasses-app SHALL connect to the configured bridge URL (WSS via Tailscale Serve) on launch using the Even Hub SDK's lifecycle. The app SHALL send a `hello` frame with the configured token and the device serial number from `bridge.getDeviceInfo()` as the `device` field. If the connection fails, the app SHALL display "Disconnected" on the HUD and retry with exponential backoff.

#### Scenario: Successful connection

- **WHEN** the user opens the glasses-app with a valid bridge URL and token configured
- **THEN** the app connects via WSS, sends the hello frame, receives acknowledgment, and displays "Connected" on the HUD

#### Scenario: Wrong token

- **WHEN** the app connects with a wrong token and the server closes with code 1008
- **THEN** the app displays "Auth failed" on the HUD and does NOT retry with the same token

### Requirement: Long-press starts voice capture

The app SHALL register a long-press touch event handler (via `onEvenHubEvent` filtering for `OsEventTypeList.CLICK_EVENT` with sufficient hold duration) that enables the glasses microphone via `bridge.audioControl(true, AudioInputSource.Glasses)` and sends an `audio.start` frame. The app SHALL display "Listening..." on the HUD during capture.

#### Scenario: User long-presses to talk

- **WHEN** the user long-presses the touchpad
- **THEN** the glasses mic activates, an `audio.start` frame is sent, and the HUD shows "Listening..."

### Requirement: Release stops voice capture and sends audio

On release after a long-press (or on `OsEventTypeList.CLICK_EVENT` release), the app SHALL disable the microphone via `bridge.audioControl(false)`, send an `audio.stop` frame, and display "Processing..." on the HUD. Accumulated PCM frames captured during the hold SHALL have been streamed as binary WS frames between `audio.start` and `audio.stop`.

#### Scenario: User releases after speaking

- **WHEN** the user releases the touchpad after saying "what time is it"
- **THEN** the mic is disabled, `audio.stop` is sent, and the HUD shows "Processing..." until a `transcript` frame arrives

### Requirement: Assistant deltas render incrementally to HUD

The app SHALL receive `assistant.delta` frames and append each delta to the accumulated assistant text container via `bridge.textContainerUpgrade({containerID, content: accumulated_text})`. The accumulated text SHALL be capped at 2000 characters (the SDK's per-call limit); if exceeded, the app SHALL flush and start a new text segment.

#### Scenario: Streaming token-by-token

- **WHEN** the server sends `assistant.delta` frames with deltas "Hello", " world", "."
- **THEN** the HUD renders "Hello", then "Hello world", then "Hello world." via successive `textContainerUpgrade` calls

### Requirement: Tool-call status displayed on HUD

The app SHALL receive `tool.start` and `tool.end` frames and display the tool name (and optional emoji/label) on a second text container designated as the "status line." On `tool.end`, the status line SHALL be cleared or updated to show completion.

#### Scenario: Tool running

- **WHEN** the server sends `tool.start` with name="web_search" and label="Searching the web"
- **THEN** the status line container renders "🔍 Searching the web..."

#### Scenario: Tool completed

- **WHEN** the server sends `tool.end` with name="web_search" and ok=true
- **THEN** the status line container is cleared (or rendered empty)

### Requirement: Double-tap interrupts current turn

The app SHALL register a double-tap handler (`OsEventTypeList.DOUBLE_CLICK_EVENT`) that sends a `stop` frame to the server to interrupt the current agent turn. The app SHALL display "Stopped" briefly on the HUD.

#### Scenario: User double-taps during agent response

- **WHEN** the agent is responding and the user double-taps the touchpad
- **THEN** a `stop` frame is sent and the HUD shows "Stopped"

### Requirement: Scroll switches sessions

The app SHALL register scroll handlers (`OsEventTypeList.SCROLL_TOP_EVENT` / `SCROLL_BOTTOM_EVENT`) that send `sessions.switch` frames with `+1` (scroll down) or `-1` (scroll up) to cycle through available Hermes sessions.

#### Scenario: User scrolls down

- **WHEN** the user scrolls down on the touchpad
- **THEN** a `sessions.switch` frame with direction=+1 is sent, and on receiving an `active` frame back, the HUD shows the new session name

### Requirement: Bridge URL and token persisted via SDK local storage

The app SHALL persist the bridge URL and token via `bridge.setLocalStorage("bridge_url", url)` and `bridge.setLocalStorage("bridge_token", token)`. On subsequent launches, the app SHALL read these via `bridge.getLocalStorage(...)` and auto-connect. Browser `localStorage` and IndexedDB SHALL NOT be used (per the device-features skill: unreliable in the Flutter WebView host).

#### Scenario: First launch configuration

- **WHEN** the user enters the bridge URL and token for the first time
- **THEN** the app persists both via `setLocalStorage` and connects

#### Scenario: Subsequent launch auto-connect

- **WHEN** the user reopens the app after a previous successful configuration
- **THEN** the app reads URL and token from `getLocalStorage` and auto-connects without prompting

### Requirement: Page containers created once at startup

The app SHALL call `bridge.createStartUpPageContainer(...)` exactly once at startup with **three** text containers:
1. Assistant reply (large, top, `isEventCapture=1`)
2. Status line (small, middle) — for tool status, "Listening...", "Processing..."
3. Session name (small, bottom) — for active session display

Subsequent updates SHALL use `textContainerUpgrade` for in-place text changes and `rebuildPageContainer` only for full layout changes (e.g., full session switch with HUD reset).

#### Scenario: Startup

- **WHEN** the app launches and `waitForEvenAppBridge()` resolves
- **THEN** `createStartUpPageContainer` is called with three text containers and the result is checked for success

#### Scenario: Updating assistant text during streaming

- **WHEN** an `assistant.delta` frame arrives
- **THEN** `textContainerUpgrade` is called (NOT `rebuildPageContainer`) to update the assistant reply container in place

### Requirement: Session name displayed in bounded container

The app SHALL display the active session's name in the dedicated session-name text container (created at startup). Names longer than 24 characters SHALL be truncated with an ellipsis (`…`) or scrolled horizontally in place. On `active` frame arrival (session switch), the container SHALL be updated via `textContainerUpgrade` to reflect the new session's name.

#### Scenario: Short session name

- **WHEN** the active session is named "Quick Chat"
- **THEN** the session-name container renders "Quick Chat"

#### Scenario: Long session name

- **WHEN** the active session is named "Research: quantum computing applications in cryptography"
- **THEN** the session-name container renders "Research: quantum comp…" (truncated) or scrolls the full name horizontally

#### Scenario: Session without name

- **WHEN** the active session has no name metadata
- **THEN** the session-name container renders the first 16 characters of the session ID

### Requirement: Background state persistence via setBackgroundState / onBackgroundRestore

The app SHALL register `setBackgroundState('glassesAppState', exporter)` and `onBackgroundRestore('glassesAppState', restorer)` at module init time (before `bridge.onEvenHubEvent`). The exporter SHALL snapshot: `accumulatedAssistantText`, `currentSessionId`, `connectionState`, `lastTranscript`. The restorer SHALL reassign each field with `??` fallback to current value, then re-render the HUD from restored state.

The WS connection itself is not serializable and will be killed during the Even Hub host's Headless WebView migration. On foreground return, the app's existing connection-retry loop SHALL re-establish the WS connection.

#### Scenario: Phone goes to background mid-stream

- **WHEN** the app is receiving `assistant.delta` frames and the phone goes to background (`sysEvent.eventType === FOREGROUND_EXIT_EVENT`)
- **THEN** the host calls the exporter, the accumulated assistant text is snapshotted, and on foreground return the restorer repopulates the state and re-renders the HUD

#### Scenario: Phone returns to foreground

- **WHEN** the host triggers `onBackgroundRestore` after the headless WebView migration
- **THEN** the app restores accumulated text, current session, connection state, and last transcript; re-renders the HUD; and the WS reconnect logic kicks in to re-establish the connection

#### Scenario: Non-serializable values not in snapshot

- **WHEN** the exporter is called
- **THEN** the snapshot contains ONLY plain JSON-serializable fields (strings, numbers, booleans, plain objects) — no WebSocket references, no class instances, no Maps

### Requirement: Best-effort foreground activation on assistant response

When an `assistant.delta` frame arrives and the app is currently backgrounded (tracked via `sysEvent.eventType === FOREGROUND_EXIT_EVENT` having fired without a subsequent `FOREGROUND_ENTER_EVENT`), the app SHALL attempt `await bridge.callEvenApp('bringToFront')` inside a try/catch. If the call succeeds (no exception), the app expects the host to bring it to the foreground. If the call throws (method not recognized or not supported), the app silently continues — the response is still rendered to the HUD via the headless WebView and will be visible when the user next opens the app.

#### Scenario: App is backgrounded, response arrives, host supports bringToFront

- **WHEN** an `assistant.delta` arrives while backgrounded and `callEvenApp('bringToFront')` does not throw
- **THEN** the host brings the app to the foreground and the user sees the streaming response

#### Scenario: App is backgrounded, response arrives, host does not support bringToFront

- **WHEN** an `assistant.delta` arrives while backgrounded and `callEvenApp('bringToFront')` throws
- **THEN** the error is caught silently, the response is rendered to the HUD via the headless WebView, and the user sees it on next foreground

#### Scenario: App is foregrounded when response arrives

- **WHEN** an `assistant.delta` arrives and the app is already foregrounded
- **THEN** `callEvenApp('bringToFront')` is NOT called (no-op, app is already visible)
