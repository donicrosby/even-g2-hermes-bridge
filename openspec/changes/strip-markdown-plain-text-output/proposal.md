## Why

Both G2 display surfaces render plain text only: the Even Add-Agent chat view (BYOA `/v1/chat/completions`) and our own glasses-app WS view (`upgradeText` raw text component, no markdown parser). The model habitually emits markdown emphasis anyway, so users see literal `**value**`, `# headers`, backticks, etc. on a 576x288 mono display. `PLUGIN_HINT` says "Markdown is supported but renders as plain text", which is both confusingly worded and insufficient — a hint is a request, not a guarantee.

Additionally, streaming correctness bug found during investigation: the adapter sends **suffix** deltas (`StreamState.delta_for`), but the glasses-app's `handleAssistantDelta` **replaces** accumulated text with the delta. Multi-edit streaming turns would render only the last fragment. And suffix-diffing alone cannot represent markdown-stripped text whose prefix changed mid-stream (e.g. partial `**bol` → `bold`).

## What Changes

- **Add `plugin/src/byoa_plugin/plaintext.py`**: `strip_markdown(text) -> str` — deterministic converter for the constructs the agent actually emits: fenced code blocks, inline code, bold/italic/strike emphasis, links (`[text](url)` → `text (url)` when text differs from url), ATX headers, blockquote markers, backslash escapes. Leaves bullets, tables, and horizontal rules untouched (they read acceptably as raw text; no scope creep).
- **Apply at both delivery sinks in `adapter.py`** (`send()` and `edit_message()`): strip the full accumulated content **before** diffing, so both the WS frames and the BYOA HTTP response (future result) carry cleaned text. This covers the chat-completions backend by construction.
- **Divergence-safe streaming**: `StreamState` gains a pure-extension check. When new cleaned text is a pure extension of what was sent, emit `assistant.delta` (suffix) as today. When it is not (marker straddling an edit boundary, or any prefix change), emit `assistant.full` (full-text resync) instead — the app already handles `assistant.full` as replace. Characterization-test semantics preserved: pure extensions still yield suffixes, unchanged text still yields empty delta, shrunk text still resends in full.
- **Fix glasses-app delta accumulation**: `handleAssistantDelta` changes from replace (`=`) to append (`+=`); `handleAssistantFull` remains replace (resync semantics).
- **Rewrite `PLUGIN_HINT`**: state plainly that the display cannot render markdown and instruct plain-text output, keeping conciseness guidance.

## Capabilities

### New Capabilities

- `plain-text-output`: Defines the plain-text contract for all assistant text leaving the plugin (WS frames and BYOA HTTP responses), the strip function's coverage, and the delta/full divergence rule.

### Modified Capabilities

- `glasses-ws-protocol`: delta-streaming requirement gains the strip-before-diff step and the `assistant.full` resync path on non-extension updates.
- `byoa-https-endpoint`: chat-completion response content is required to be markdown-stripped.

## Impact

**Affected code:**

- `plugin/src/byoa_plugin/plaintext.py` — new module.
- `plugin/src/byoa_plugin/connections.py` — `StreamState` extension check (keeps `delta_for` contract).
- `plugin/src/byoa_plugin/adapter.py` — strip at both sinks; full-frame resync; BYOA future resolves with cleaned text.
- `plugin/src/byoa_plugin/__init__.py` — `PLUGIN_HINT` rewrite.
- `glasses-app/src/main.ts` — `handleAssistantDelta` append fix.
- Tests: new `plugin/tests/test_plaintext.py`; cases added to `test_stream_state.py`, adapter/BYOA tests; glasses-app delta-accumulation test.

**No protocol changes**: `assistant.full` frame already exists in the wire schema and the app already handles it.

**Rollback risk**: low. Stripping is a pure function with characterization tests; delta semantics changes are additive (new resync branch), existing suffix behavior preserved.

**Out of scope**: tables, horizontal rules, bullet restyling (render acceptably raw); any glasses-app markdown renderer (Even text containers are plain-text by API).
