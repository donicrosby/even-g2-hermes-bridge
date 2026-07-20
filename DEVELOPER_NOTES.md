# Hermes App for G2

A clean, SDK-native app for Even Realities G2 smart glasses. Renders UI on the glasses display, not just the phone's WebView preview.

## Architecture

1. **Glasses page containers** — `createStartUpPageContainer()` creates native containers at fixed coordinates on the glasses display (576×288 canvas). These are separate from the HTML DOM.
2. **Phone WebView preview** — Shows a preview of `index.html` for development/debugging, but the glasses render independently.
3. **WebSocket bridge** — Connects to the Hermes bridge server (`hermes.local:8765`) for audio streaming and LLM responses.

## Key SDK Methods

- `createStartUpPageContainer(container)` — Creates the glasses page with containers. Call this BEFORE any other UI operations.
- `rebuildPageContainer(container)` — Replaces the current page with a new container layout.
- `textContainerUpgrade(container)` — Updates text content in place (faster than rebuild).
- `updateImageRawData(data)` — Updates image container content.
- `audioControl(true, AudioInputSource.Glasses)` — Enable microphone on glasses.
- `onEvenHubEvent(callback)` — Listen for audio, touch, and system events.

## Container System

- Container IDs are zero-indexed (0, 1, 2...). IDs 0–N are assigned sequentially.
- At most 12 containers per page.
- At most 8 text containers.
- Exactly one container should have `isEventCapture: 1` to receive touch events.
- Coordinate origin is top-left.

## Files

- `src/main.ts` — App logic, SDK calls, WebSocket bridge
- `index.html` — HTML page for phone WebView preview (loaded by Vite dev server)
- `app.json` — Even Hub manifest with package_id and entry configuration
