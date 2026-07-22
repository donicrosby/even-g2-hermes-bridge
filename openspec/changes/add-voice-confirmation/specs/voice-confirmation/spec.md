## ADDED Requirements

### Requirement: Plugin does not auto-forward voice transcripts to the gateway

The plugin SHALL send a `transcript` frame to the glasses-app after ASR completes, but SHALL NOT call `handle_message` or otherwise forward the transcript to the Hermes Gateway. The transcript reaches the gateway ONLY when the glasses-app sends an explicit `text` frame containing the transcript content.

Rationale: the plugin is a transcription pipe, not a decision-maker. The glasses-app owns the decision of when (or whether) to send the user's speech to the agent.

#### Scenario: Voice transcript arrives at the glasses-app
- **WHEN** the plugin completes ASR and sends a `transcript` frame
- **THEN** the glasses-app SHALL receive the transcript text
- **AND** the plugin SHALL NOT have forwarded the transcript to the gateway
- **AND** no agent response SHALL be in-flight

#### Scenario: Glasses-app sends confirmed text
- **WHEN** the glasses-app sends a `text` frame (via confirm press, auto-send timer, or fast-mode immediate forward)
- **THEN** the plugin's `_on_text` handler SHALL forward it to the gateway via `handle_message`
- **AND** the gateway SHALL process it as a normal text message

### Requirement: Voice confirmation mode is configurable

The glasses-app SHALL support two voice confirmation modes, stored in `GlassesAppState` via SDK storage:
- `fast`: on receiving a `transcript` frame, immediately send a `text` frame with the transcript content. No overlay, no delay.
- `careful`: on receiving a `transcript` frame, display the transcript with a confirmation overlay. Wait for user input or auto-send timer.

The mode SHALL default to `careful`. The mode SHALL be configurable via the settings screen and SHALL persist across app restarts via SDK storage.

#### Scenario: Fast mode immediate send
- **WHEN** `voiceConfirmMode === 'fast'` and a `transcript` frame arrives
- **THEN** the glasses-app SHALL immediately send a `text` frame with the transcript content
- **AND** SHALL NOT show a confirmation overlay

#### Scenario: Careful mode shows overlay
- **WHEN** `voiceConfirmMode === 'careful'` and a `transcript` frame arrives
- **THEN** the glasses-app SHALL display the transcript text in the assistant container
- **AND** SHALL display `">Confirm  Retry"` in the status container
- **AND** SHALL NOT send a `text` frame until the user confirms or the auto-send timer fires

#### Scenario: Mode persists across restarts
- **WHEN** the user changes the mode in settings and restarts the app
- **THEN** the mode SHALL be restored from SDK storage via `restoreState()`

### Requirement: Auto-send timer is configurable

The glasses-app SHALL support an auto-send timer (`voiceAutoSendSec`) that, in careful mode, automatically sends the `text` frame after the configured number of seconds if the user has not interacted. The timer SHALL default to 15 seconds. A value of 0 SHALL disable auto-send entirely (wait for manual confirm only).

#### Scenario: Auto-send fires
- **WHEN** `voiceConfirmMode === 'careful'` and `voiceAutoSendSec > 0`
- **AND** the user has not pressed confirm or retry within `voiceAutoSendSec` seconds
- **THEN** the glasses-app SHALL send the `text` frame with the pending transcript
- **AND** SHALL clear the confirmation overlay

#### Scenario: Auto-send disabled
- **WHEN** `voiceAutoSendSec === 0`
- **THEN** the glasses-app SHALL wait indefinitely for user input
- **AND** SHALL NOT auto-send

### Requirement: Confirm gesture sends the transcript

In careful mode, a press gesture (sysEvent eventType 0) while the confirmation overlay is shown SHALL send a `text` frame with the pending transcript content, clear the overlay, and restore the normal display.

#### Scenario: User presses to confirm
- **WHEN** the confirmation overlay is shown and `pendingTranscript !== null`
- **AND** the user presses the touchpad (sysEvent 0)
- **THEN** the glasses-app SHALL send `text(pendingTranscript)` to the plugin
- **AND** SHALL set `pendingTranscript = null`
- **AND** SHALL clear the overlay (restore normal assistant + status container content)

### Requirement: Retry gesture restarts voice capture

In careful mode, a swipe-down gesture (textEvent eventType 2) while the confirmation overlay is shown SHALL clear the overlay and restart voice capture (equivalent to calling `toggleMic()` to start a new recording).

#### Scenario: User swipes down to retry
- **WHEN** the confirmation overlay is shown and `pendingTranscript !== null`
- **AND** the user swipes down (textEvent 2)
- **THEN** the glasses-app SHALL set `pendingTranscript = null`
- **AND** SHALL clear the overlay
- **AND** SHALL start a new voice capture (call `toggleMic()` or equivalent)
