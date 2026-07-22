## Why

In noisy environments or when ASR quality is inconsistent, the user has no way to verify what was transcribed before it reaches the agent. The current flow auto-forwards every voice transcript to the LLM immediately — if the transcript is wrong, the user wastes a turn getting a response to something they didn't say. A "listen → display → confirm → send" flow lets the user catch transcription errors before they cost a round-trip.

## What Changes

- **Plugin**: `_handle_voice` stops auto-forwarding voice transcripts to the gateway. It sends the `transcript` frame to the glasses-app and returns — no `handle_message` call, no timeout, no fallback. The transcript reaches the gateway ONLY when the glasses-app sends an explicit `text` frame.
- **Glasses-app**: new `pendingTranscript` state. On receiving a `transcript` frame, the app enters one of two modes (configurable):
  - **fast** (default off): immediately sends `text(transcript)` — same latency as today, one extra ~10ms round-trip.
  - **careful** (default on): shows the transcript with a `>Confirm  Retry` overlay. Press = send `text(transcript)`. Swipe down = restart mic. Optional auto-send timer (default 15s; 0 = never).
- **Settings**: two new fields in `GlassesAppState` — `voiceConfirmMode: 'fast' | 'careful'` and `voiceAutoSendSec: number`. Persisted via SDK storage (same pattern as bridge credentials). Configurable via the settings screen.
- **No protocol changes, no new frame types.** The glasses-app sends a regular `text` frame on confirm — same path as typed input. The plugin's `_on_text` handler forwards it to the gateway unchanged.

## Capabilities

### New Capabilities
- `voice-confirmation`: Defines the transcript confirmation overlay, the fast/careful mode toggle, the auto-send timer, and the gesture mappings (press=confirm, swipe=retry).

## Impact

**Affected code:**
- `plugin/src/byoa_plugin/adapter.py` — `_handle_voice`: remove auto-forward (delete lines that call `handle_message` after sending transcript)
- `glasses-app/src/main.ts` — `handleTranscript`: branch on mode; new `pendingTranscript` state; overlay rendering; gesture handling; auto-send timer
- `glasses-app/src/lib/state.ts` — add `voiceConfirmMode` + `voiceAutoSendSec` to `GlassesAppState`
- `glasses-app/src/main.ts` config screen — add voice mode toggle + auto-send input

**No protocol changes**: same WS frames, just different timing on the glasses-app side.

**Runtime behavior change**: voice transcripts no longer auto-forward. In fast mode the delay is imperceptible (~10ms). In careful mode the user gets a confirmation step that catches transcription errors before they reach the LLM.
