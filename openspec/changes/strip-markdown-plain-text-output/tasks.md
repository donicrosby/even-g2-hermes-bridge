## 1. Strip function

- [ ] 1.1 Create `plugin/src/byoa_plugin/plaintext.py` with `strip_markdown(text: str) -> str`. Coverage: fenced code blocks (``` and ~~~, strip fences keep body), inline code backticks, `**bold**`/`__bold__`, `*italic*`/`_italic_`, `~~strike~~`, links `[text](url)` → `text (url)` when text != url else `text`, ATX headers `#`–`######` + trailing `#`s, blockquote `> ` markers, backslash escapes of punctuation. Leaves bullets `- `/`* `, tables `|`, horizontal rules `---` untouched.
- [ ] 1.2 `strip_markdown` idempotent: stripping already-stripped text returns it unchanged.
- [ ] 1.3 New `plugin/tests/test_plaintext.py` covering every construct + idempotency + mixed multiline prose.

## 2. Delivery sinks

- [ ] 2.1 `adapter.send()`: strip full content before `state.delta_for()`. BYOA future resolves with the **cleaned** text. WS push uses cleaned text.
- [ ] 2.2 `adapter.edit_message()`: strip full content before diffing; delta/full decision and frame emission use cleaned text; `finalize` resolves BYOA future with cleaned text.
- [ ] 2.3 Adapter/BYOA tests updated: markdown-bearing content produces stripped frames/responses.

## 3. Divergence-safe streaming

- [ ] 3.1 `StreamState` pure-extension check: if cleaned text startswith last-sent cleaned text → suffix `assistant.delta` as today. Else → `assistant.full` resync. Preserve characterization semantics: first call full, pure extension suffix, unchanged empty, shrunk full-replace.
- [ ] 3.2 Extend `test_stream_state.py` with extension/resync cases.
- [ ] 3.3 Fix `glasses-app/src/main.ts` `handleAssistantDelta`: `accumulatedAssistantText += frame.text` (append). `handleAssistantFull` stays replace. Add vitest coverage.

## 4. Hint

- [ ] 4.1 Rewrite `PLUGIN_HINT` in `plugin/src/byoa_plugin/__init__.py`: display cannot render markdown; reply in plain text; keep concise/scannable. Drop "Markdown is supported but renders as plain text".

## 5. Validation + ship

- [ ] 5.1 `uv run pytest` green (plugin), `npm test` green (glasses-app), `ruff check` + `basedpyright` clean.
- [ ] 5.2 Live BYOA turn returns stripped content; sync to `/opt/data/plugins/even-g2/src`; container restart not required (plugin reloads on demand — verify).
- [ ] .2 verify installed copy behavior via `/v1/chat/completions` with markdown-prompting message.
- [ ] 5.3 Commit (`feat(plugin)` + `fix(glasses-app)` atomic), push, archive OpenSpec change.
