## MODIFIED Requirements

### Requirement: Delta streaming via StreamState

The server SHALL compute incremental text deltas using `StreamState.delta_for(accumulated_text)` which strips the trailing streaming cursor (` ▉`) and returns only the unsent suffix. The server SHALL emit each non-empty delta as an `assistant.delta` frame.

The accumulated text SHALL first be passed through `strip_markdown()` (see `plain-text-output` capability) so all emitted deltas are plain text. When the cleaned accumulated text is not a pure extension of the last-sent cleaned text, the server SHALL emit `assistant.full` with the complete cleaned text instead of a suffix delta.

#### Scenario: First send_message

- **WHEN** `StreamState.sent_len=0` and the adapter receives `send_message(chat_id, "Hello")`
- **THEN** `delta_for("Hello")` returns `"Hello"`, which is emitted as `assistant.delta`

#### Scenario: Subsequent edit_message

- **WHEN** `StreamState.sent_len=5` and the adapter receives `edit_message(chat_id, msg_id, "Hello world")`
- **THEN** `delta_for("Hello world")` returns `" world"`, which is emitted as `assistant.delta`

#### Scenario: Cursor stripped before diffing

(preserves existing behavior; unchanged by this change)

#### Scenario: Markdown stripped before diffing

- **WHEN** the adapter receives `edit_message(chat_id, msg_id, "**bold** text")` with last-sent cleaned text `"bold"`
- **THEN** the emitted frame SHALL be `assistant.delta(" text")` — computed from stripped text

#### Scenario: Emphasis marker straddling edit boundary

- **WHEN** last-sent cleaned text is `"bo"` and the new accumulated content is `"**bold** done"`
- **THEN** the cleaned text is `"bold done"` which is not an extension of `"bo"` at the divergent prefix
- **AND** the server SHALL emit `assistant.full("bold done")` as resync
