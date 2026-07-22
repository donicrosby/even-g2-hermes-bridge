## 1. Plugin: stop auto-forwarding voice transcripts

- [ ] 1.1 In `plugin/src/byoa_plugin/adapter.py`, remove the `handle_message` call from `_handle_voice`. After sending `transcript(text)`, the method returns. No timeout, no fallback.
- [ ] 1.2 Remove the `MessageType.VOICE` import if no longer used elsewhere in the file.
- [ ] 1.3 Run `uv run pytest -q` — verify existing tests still pass (the integration tests that send audio + expect a response will need the glasses-app to send a `text` frame instead; update if needed).

## 2. Glasses-app: state + settings

- [ ] 2.1 Add `voiceConfirmMode: 'fast' | 'careful'` and `voiceAutoSendSec: number` to `GlassesAppState` in `glasses-app/src/lib/state.ts`. Update `serializeState` + `mergeState`.
- [ ] 2.2 Add `let voiceConfirmMode: 'fast' | 'careful' = 'careful'` and `let voiceAutoSendSec = 15` as module-level variables in `main.ts`.
- [ ] 2.3 Update `currentMutableState()` + `restoreState()` to include the new fields.
- [ ] 2.4 Add unit tests in `tests/state.test.ts` for the new fields.

## 3. Glasses-app: handleTranscript branching

- [ ] 3.1 Add `let pendingTranscript: string | null = null` and `let autoSendTimer: ReturnType<typeof setTimeout> | null = null` as module-level state.
- [ ] 3.2 Rewrite `handleTranscript()`: if `voiceConfirmMode === 'fast'`, immediately send `text(transcript)` via `sendFrame`. If `'careful'`, set `pendingTranscript`, update the assistant container to show the transcript, update the status container to show `">Confirm  Retry"`, and start the auto-send timer (if `voiceAutoSendSec > 0`).
- [ ] 3.3 Add `clearTranscriptOverlay()` helper: clears `pendingTranscript`, cancels the auto-send timer, restores normal assistant + status container content.

## 4. Glasses-app: gesture handling for confirm/retry

- [ ] 4.1 In the event handler, add a branch at the top: if `pendingTranscript !== null`:
  - sysEvent eventType 0 (press) → `sendFrame(wireText(pendingTranscript), 'text')` + `clearTranscriptOverlay()`
  - textEvent eventType 2 (swipe down) → `clearTranscriptOverlay()` + `toggleMic()`
  - All other events → ignored while overlay is active (no session switching, no double-tap exit)

## 5. Glasses-app: config screen additions

- [ ] 5.1 Add a "Voice confirmation" section to the config overlay with:
  - Mode toggle: `( Careful ) Fast` (clicking toggles between the two)
  - Auto-send input: `[15] seconds (0 = off)`
- [ ] 5.2 Update the settings-save handler to read the new values + update state variables + `void saveState()`.

## 6. Verify

- [ ] 6.1 `cd plugin && uv run pytest -q` — all tests pass.
- [ ] 6.2 `cd glasses-app && npm run test && npm run typecheck && npm run lint` — all clean.
- [ ] 6.3 `cd glasses-app && npm run release` — build + pack succeeds.
- [ ] 6.4 Manual smoke: careful mode — speak, see transcript, press confirm, verify agent responds. Swipe retry, verify mic restarts. Fast mode — speak, verify immediate response (no overlay).
