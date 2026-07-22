## Context

The voice flow today: glasses-app captures audio → sends PCM to plugin → plugin transcribes → plugin sends `transcript` frame to glasses-app AND immediately calls `handle_message` to forward to the gateway. The transcript reaches the LLM before the user sees it.

The change decouples transcription from forwarding: the plugin transcribes and displays, the glasses-app decides when (or whether) to forward.

### Current voice path in the plugin (adapter.py:267-297)

```python
async def _handle_voice(self, chat_id, pcm):
    text = transcribe(pcm, self.cfg)
    await self.registry.send_frame(chat_id, proto.transcript(text))   # ← display
    event = MessageEvent(text=text, message_type=MessageType.VOICE)
    await self.handle_message(event)                                   # ← auto-forward (REMOVE)
```

After the change:

```python
async def _handle_voice(self, chat_id, pcm):
    text = transcribe(pcm, self.cfg)
    await self.registry.send_frame(chat_id, proto.transcript(text))   # ← display
    # done. glasses-app sends text(transcript) when user confirms.
```

## Goals / Non-Goals

**Goals:**
- User can verify transcription before it reaches the agent.
- Mode is configurable (fast vs. careful).
- Auto-send timeout is configurable.
- No protocol changes, no new frame types.

**Non-Goals:**
- Editing the transcript before sending (future work — would need a text input on the glasses display, which the SDK doesn't support well).
- Confirmation for BYOA mode (Even's Add Agent has its own UX we can't control).
- ASR quality scoring (future work — would let the app auto-skip confirmation for high-confidence transcripts).

## Decisions

### D1: Plugin never auto-forwards voice transcripts

**Choice.** Remove the `handle_message` call from `_handle_voice`. The plugin sends the transcript frame and returns. The transcript reaches the gateway ONLY via an explicit `text` frame from the glasses-app.

**Rationale.** The plugin is a dumb pipe — it transcribes and displays. The glasses-app is the brain that decides what to send. No "helpful" auto-forward that might send something the user didn't want.

### D2: Voice path and text path unify

**Choice.** Both voice-originated and typed messages reach the gateway via the same `_on_text` → `handle_message` path. The `MessageType.VOICE` type is no longer used for forwarded messages — everything is `MessageType.TEXT`.

**Rationale.** Simpler. The gateway shouldn't need to distinguish voice vs. typed for message routing. If voice-specific metadata is needed later, it can be added as a field on the text frame.

### D3: Fast mode sends text frame immediately

**Choice.** In fast mode, `handleTranscript` calls `sendFrame(wireText(transcript), 'text')` immediately on receiving the transcript frame. No overlay, no delay beyond the ~10ms WS round-trip.

**Rationale.** Fast mode should be indistinguishable from today's behavior from the user's perspective.

### D4: Careful mode overlay uses existing containers

**Choice.** The overlay reuses the existing assistant + status containers via `textContainerUpgrade` (no `rebuildPageContainer`). The assistant container shows `"You said:\n<transcript>"`. The status container shows `">Confirm  Retry"`.

**Rationale.** Avoids the destructive `rebuildPageContainer` we explicitly banned in the `glasses-app-page-lifecycle` spec. `textContainerUpgrade` is flicker-free and doesn't reset container state.

### D5: Gesture mapping

| Gesture | Event | Action |
|---|---|---|
| Press | sysEvent 0 | Confirm → send `text(transcript)` + clear overlay |
| Swipe down | textEvent 2 | Retry → clear overlay + call `toggleMic()` |

**Rationale.** Press = "yes, send it" is the natural affirmative gesture. Swipe down = "go back, try again" is the natural dismissive gesture. Matches the handle-input skill's event model.

### D6: Auto-send timer lives on the glasses-app side

**Choice.** The auto-send timer (`voiceAutoSendSec`) runs in the glasses-app, not the plugin. When it fires, the glasses-app sends `text(transcript)` as if the user pressed confirm. The plugin has no timeout of its own.

**Rationale.** The user's preference (timeout value) is a UX setting. It belongs in the glasses-app alongside the other voice settings. The plugin doesn't need to know about it.

### D7: Settings stored in GlassesAppState

**Choice.** `voiceConfirmMode: 'fast' | 'careful'` and `voiceAutoSendSec: number` are added to `GlassesAppState`, serialized via `bridge.setLocalStorage(STATE_KEY)` — same pattern as bridge credentials and app state. The config screen gets two new inputs.

**Rationale.** Consistent with the credential persistence pattern. Survives app restarts via SDK storage.

## Risks

- **[Risk: user in fast mode doesn't realize transcripts are going through an extra round-trip]** → *Mitigation*: the round-trip is ~10ms over LAN/Tailscale. Not perceptible.
- **[Risk: careful mode adds friction for power users]** → *Mitigation*: fast mode is one toggle away. Auto-send at 5s makes careful mode nearly as fast.
- **[Risk: glasses-app crashes during careful mode, transcript lost]** → *Mitigation*: acceptable. User speaks again. The plugin doesn't hold state — it already sent the transcript frame and moved on.
