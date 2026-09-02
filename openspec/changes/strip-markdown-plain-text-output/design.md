## Context

Two consumers of assistant text, both plain-text-only:

1. **BYOA HTTP** (`/v1/chat/completions`): Even's Add-Agent overlay renders `choices[0].message.content` verbatim.
2. **WS glasses-app**: `upgradeText(cid, name, text)` — Even Hub text container API is plain text; no markdown parser in the app.

The gateway streams accumulated text via `edit_message`; the adapter suffix-diffs and emits `assistant.delta`. Suffix diffs assume append-only text. Markdown stripping mutates the prefix when an emphasis marker straddles an edit boundary (`**bo` → `**bold**`), breaking that assumption.

## Goals / Non-Goals

Goals: deterministic markdown→plaintext conversion at the adapter boundary; correct streaming under prefix mutation; chat-completions backend included by construction; hint rewritten to match reality.

Non-Goals: rendering markdown on device (impossible via Even text container API), tables/HR/bullet restyling, gateway-side display transforms.

## Decisions

**D1 — strip at the adapter, full-text, before diffing.** Single choke point both sinks pass through (`send()` / `edit_message()`). Stripping the full accumulated text each edit is O(n) per edit with n ≤ a few KB — negligible. Stripping the delta alone cannot work: marker pairs span chunk boundaries.

**D2 — divergence rule: extension → suffix delta; else → `assistant.full`.** `assistant.full` already exists in the wire protocol and the app already treats it as replace. Resync on divergence is simpler and more robust than reserving pending markers, and it heals any drift. `delta_for()` keeps its characterization contract (first-call full, extension suffix, unchanged empty, shrunk full) — the extension check layers on top without breaking it.

**D3 — glasses-app appends deltas.** `handleAssistantDelta` currently replaces (`=`) with suffix deltas — a latent rendering bug only masked by single-delta turns. Append (`+=`) matches the adapter's suffix contract; `handleAssistantFull` remains replace.

**D4 — link handling `[text](url)` → `text (url)` only when text != url.** Bare `[url](url)` collapses to `url`. Avoids `https://x (https://x)` duplication.

**D5 — PLUGIN_HINT states inability, not support.** "Markdown is supported but renders as plain text" invites markdown. Rewritten: the display cannot render markdown, reply in plain text, keep concise.

## Risks / Trade-offs

- **Aggressive stripping mangles legit content** (e.g. code blocks the user wants verbatim). Mitigation: strip only emphasis/fence/header/quote/link markers — body text always preserved; idempotency test.
- **`assistant.full` resync replaces app text mid-turn** — same visual result as a delta, one frame instead of two; acceptable on a 200-char display.
- **Asterisk-heavy prose** (e.g. `* * *` or C `a*b`): single `*` pairs could be eaten as italic. Mitigation: emphasis matching requires non-space adjacent chars (CommonMark-ish heuristics), tests for `a*b`, `2*3*4`, wildcard globs.

## Risks referenced from investigation

- The gateway "duplicate send" warning on the non-streaming path is upstream behavior; not touched.

## Open Questions

- None blocking. Tables/HR left raw by decision (read fine as text).
