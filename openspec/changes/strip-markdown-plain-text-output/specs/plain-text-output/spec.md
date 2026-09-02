## ADDED Requirements

### Requirement: All assistant output text SHALL be markdown-stripped plain text

The plugin SHALL convert assistant message content from markdown to plain text before it leaves the plugin boundary — both WebSocket frames (`assistant.delta`, `assistant.full`) and BYOA HTTP responses (`choices[0].message.content`) SHALL contain stripped text. The stripping SHALL be performed by `strip_markdown()` in `plugin/src/byoa_plugin/plaintext.py` applied to the full accumulated content at the delivery sinks (`adapter.send()` and `adapter.edit_message()`), before suffix-diffing.

#### Scenario: Bold emphasis stripped in BYOA response

- **WHEN** the gateway finalizes a turn whose content is `"Set **temp** to 21"`
- **THEN** the BYOA HTTP response `choices[0].message.content` SHALL equal `"Set temp to 21"`

#### Scenario: Inline code stripped in WS frame

- **WHEN** `edit_message(finalize=True)` receives content containing `` `uv run pytest` ``
- **THEN** the emitted WS frame text SHALL contain `uv run pytest` without backticks

#### Scenario: Stripping applied per accumulated edit, not per delta

- **WHEN** streaming edits arrive as `"**bo"`, then `"**bold** done"`
- **THEN** the frames emitted SHALL be based on stripped full text (`"bo"` suffix… resync per divergence rule), never on independently stripped deltas

### Requirement: strip_markdown coverage and safety

`strip_markdown()` SHALL remove: fenced code block markers (``` and ~~~ fences, body preserved), inline code backticks, bold (`**x**`, `__x__`), italic (`*x*`, `_x_`), strikethrough (`~~x~~`), link syntax (`[text](url)` → `text (url)` when text differs from url, else `url`), ATX header markers (`#`–`######` incl. closing sequence), blockquote markers (`> `), and backslash escapes. It SHALL leave untouched: bullet markers (`- `, `* `, `+ `), table pipes, horizontal rules, and ordinary punctuation. Emphasis matching SHALL NOT consume single asterisks/underscores adjacent to whitespace or digits where CommonMark would not treat them as emphasis (e.g. `2*3*4` unchanged).

`strip_markdown()` SHALL be idempotent: `strip_markdown(strip_markdown(x)) == strip_markdown(x)`.

#### Scenario: Intra-word asterisks preserved

- **WHEN** input is `"Multiply 2*3*4"`
- **THEN** output SHALL be `"Multiply 2*3*4"` unchanged

#### Scenario: Link with distinct text

- **WHEN** input is `"See [docs](https://example.com)"`
- **THEN** output SHALL be `"See docs (https://example.com)"`

#### Scenario: Idempotency

- **WHEN** input is any already-stripped string from the coverage set
- **THEN** a second application SHALL return it unchanged

### Requirement: Divergence-safe streaming (extension → delta, else full resync)

`StreamState` SHALL track the last-sent cleaned text. When new cleaned text is a pure extension (startswith last-sent), the adapter SHALL emit the suffix as `assistant.delta`. When it is not (prefix changed, marker straddling, shrink), the adapter SHALL emit `assistant.full` with the complete cleaned text. The existing `delta_for()` characterization contract SHALL be preserved: first call returns full text, pure extension returns suffix, unchanged returns empty, shrink returns full text with cursor reset.

#### Scenario: Prefix mutation triggers resync

- **WHEN** last-sent cleaned text is `"Answer: bo"` and new cleaned text is `"Answer: bold done"`
- **THEN** the adapter SHALL emit `assistant.full("Answer: bold done")`, not a suffix delta

#### Scenario: Plain extension still suffix deltas

- **WHWEN** last-sent is `"Answer: bo"` and new is `"Answer: bold done"`
- **THEN** the adapter SHALL emit `assistant.delta("ld done")`

### Requirement: Glasses-app accumulates suffix deltas by appending

The glasses-app `handleAssistantDelta` SHALL append `frame.text` to `accumulatedAssistantText` (suffix-delta contract); `handleAssistantFull` SHALL replace `accumulatedAssistantText` wholesale (resync contract). Empty delta text SHALL be a no-op.

#### Scenario: Two sequential deltas

- **WHEN** frames `assistant.delta("Hello ")` then `assistant.delta("world")` arrive
- **THEN** `accumulatedAssistantText` SHALL equal `"Hello world"`

#### Scenario: Full resync after deltas

- **WHEN** `assistant.full("corrected")` arrives after prior deltas
- **THEN** `accumulatedAssistantText` SHALL equal `"corrected"`
